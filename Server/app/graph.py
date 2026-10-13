import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict

from app import prompts
from app.config import Settings
from app.retrieval import Evidence
from app.schemas import Source

ABSTAIN = (
    "No encontré suficiente información en las fuentes disponibles para responder "
    "con respaldo. Puedes precisar tu pregunta o consultar a un profesional que conozca "
    "tu situación. No puedo determinar un diagnóstico personal."
)


class ModelSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Plan(ModelSchema):
    route: Literal["knowledge", "conversation", "out_of_scope", "urgent"]
    query: str


class Selection(ModelSchema):
    ids: list[int]
    sufficient: bool


class Draft(ModelSchema):
    answer: str
    citations: list[int]
    insufficient_evidence: bool


class Verification(ModelSchema):
    supported: bool
    reason: str


class GraphState(TypedDict, total=False):
    question: str
    history: list[dict]
    audience: str
    style: str
    plan: Plan
    candidates: list[Evidence]
    evidence: list[Evidence]
    draft: Draft
    answer: str
    sources: list[Source]
    evidence_status: str
    attempts: int
    repair_reason: str
    verified: bool


def citation_error(draft: Draft, evidence_count: int) -> str | None:
    declared = set(draft.citations)
    inline = {int(value) for value in re.findall(r"\[(\d+)\]", draft.answer)}
    # Ban model-generated links and grouped/ranged references that escape the numeric validator.
    malformed = any(
        not re.fullmatch(r"\d+", value) for value in re.findall(r"\[([^\]]+)\]", draft.answer)
    )
    if malformed or re.search(r"https?://|www\.", draft.answer, re.IGNORECASE):
        return "Usa únicamente citas individuales [n] y no incluyas enlaces."
    if not declared or declared != inline or any(n < 1 or n > evidence_count for n in declared):
        return "Todas las citas deben existir y coincidir con los IDs declarados."
    if len(draft.answer) > 6000 or not draft.answer.strip():
        return "La respuesta debe ser breve y contener texto."
    return None


def evidence_payload(items: list[Evidence]) -> list[dict]:
    return [
        {
            "id": index,
            "title": item.title,
            "page": item.page,
            "trust_level": item.trust_level,
            "text": item.content,
        }
        for index, item in enumerate(items, start=1)
    ]


def build_graph(models, retriever, settings: Settings):
    async def plan(state):
        result = await models.call(
            Plan,
            prompts.PLAN,
            {
                "question": state["question"],
                "history": state["history"],
            },
        )
        # Bound rewritten queries independently of provider schema adherence.
        result.query = result.query.strip()[:1000] or state["question"][:1000]
        return {"plan": result, "attempts": 0, "repair_reason": ""}

    async def retrieve(state):
        return {"candidates": await retriever.search(state["plan"].query)}

    async def select_evidence(state):
        candidates = state["candidates"]
        if not candidates:
            return {"evidence": []}
        selection = await models.call(
            Selection,
            prompts.SELECT,
            {
                "question": state["question"],
                "query": state["plan"].query,
                "fragments": evidence_payload(candidates),
            },
        )
        ids = list(dict.fromkeys(selection.ids))
        if not selection.sufficient or any(n < 1 or n > len(candidates) for n in ids):
            return {"evidence": []}
        return {"evidence": [candidates[n - 1] for n in ids[: settings.retrieval_top_k]]}

    async def generate(state):
        draft = await models.call(
            Draft,
            prompts.ANSWER,
            {
                "question": state["question"],
                "audience": state["audience"],
                "resolved_query": state["plan"].query,
                "style": state["style"],
                "fragments": evidence_payload(state["evidence"]),
                "repair_reason": state["repair_reason"],
            },
            role="answer",
        )
        return {"draft": draft, "attempts": state["attempts"] + 1}

    async def verify(state):
        draft = state["draft"]
        if draft.insufficient_evidence:
            return {"verified": False, "repair_reason": "insufficient", "attempts": 2}
        error = citation_error(draft, len(state["evidence"]))
        if error:
            return {"verified": False, "repair_reason": error}
        result = await models.call(
            Verification,
            prompts.VERIFY,
            {
                "question": state["question"],
                "answer": draft.answer,
                "resolved_query": state["plan"].query,
                "fragments": evidence_payload(state["evidence"]),
            },
        )
        return {"verified": result.supported, "repair_reason": result.reason[:1000]}

    async def finalize(state):
        cited = set(state["draft"].citations)
        sources = [
            Source(
                citation=n,
                document_id=item.document_id,
                chunk_id=item.chunk_id,
                title=item.title,
                page=item.page,
                url=item.url,
                trust_level=item.trust_level,
                excerpt=item.content[:600],
            )
            for n, item in enumerate(state["evidence"], start=1)
            if n in cited
        ]
        return {"answer": state["draft"].answer, "sources": sources, "evidence_status": "grounded"}

    async def abstain(state):
        return {"answer": ABSTAIN, "sources": [], "evidence_status": "insufficient"}

    async def direct(state):
        responses = {
            "conversation": (
                "Hola, soy Autism Insight. Puedo ayudarte a explorar información sobre "
                "autismo, comunicación, accesibilidad y apoyo cotidiano. "
                "¿Qué te gustaría conversar?"
            ),
            "out_of_scope": (
                "Mi enfoque es el autismo, la comunicación y la accesibilidad. "
                "Puedo ayudarte con una pregunta relacionada con esos temas."
            ),
            "urgent": (
                "Lo que cuentas merece apoyo inmediato. Si tú u otra persona están en "
                "peligro ahora, contacta los servicios de emergencia de tu zona o acude "
                "a urgencias. Si puedes, busca a una persona de confianza que te acompañe."
            ),
        }
        return {
            "answer": responses[state["plan"].route],
            "sources": [],
            "evidence_status": "not_applicable",
        }

    graph = StateGraph(GraphState)
    for name, node in (
        ("plan", plan),
        ("retrieve", retrieve),
        ("select", select_evidence),
        ("generate", generate),
        ("verify", verify),
        ("finalize", finalize),
        ("abstain", abstain),
        ("direct", direct),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", lambda s: "retrieve" if s["plan"].route == "knowledge" else "direct"
    )
    graph.add_edge("retrieve", "select")
    graph.add_conditional_edges("select", lambda s: "generate" if s["evidence"] else "abstain")
    graph.add_edge("generate", "verify")
    graph.add_conditional_edges(
        "verify",
        lambda s: "finalize" if s["verified"] else ("generate" if s["attempts"] < 2 else "abstain"),
    )
    for name in ("finalize", "abstain", "direct"):
        graph.add_edge(name, END)
    # Each invocation has fresh graph state; durable, authorized history is loaded by the service.
    return graph.compile()
