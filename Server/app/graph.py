"""Flujo RAG de LangGraph, con rutas explícitas y una reparación máxima.

Cada pregunta recibe estado nuevo. El historial autorizado procede del
servicio; este módulo no persiste mensajes ni utiliza un checkpointer.
Los esquemas de salida del modelo se explican con comentarios para no
cambiar sus descripciones enviadas al proveedor al documentarlos.
"""

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


# Base de las salidas del modelo: rechaza campos no previstos en su contrato.
class ModelSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Decisión de ruta y consulta autónoma para resolver preguntas de seguimiento.
class Plan(ModelSchema):
    route: Literal["knowledge", "conversation", "out_of_scope", "urgent"]
    query: str


# IDs locales de fragmentos seleccionados y evaluación de suficiencia del modelo.
class Selection(ModelSchema):
    ids: list[int]
    sufficient: bool


# Borrador todavía no publicable: texto, citas declaradas y señal de falta de evidencia.
class Draft(ModelSchema):
    answer: str
    citations: list[int]
    insufficient_evidence: bool


# Dictamen del modelo verificador; reason permite corregir el siguiente borrador.
class Verification(ModelSchema):
    supported: bool
    reason: str


class GraphState(TypedDict, total=False):
    """Datos compartidos por los nodos de una sola pregunta; cada nodo añade sus resultados."""

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
    """Devuelve una explicación si las referencias del borrador son inválidas, o None.

    Comprueba IDs disponibles, coincidencia entre citas declaradas y escritas,
    formato individual [n], ausencia de enlaces y longitud del texto.
    No verifica hechos: esa tarea corresponde al nodo verify y su modelo.
    """
    declared = set(draft.citations)
    inline = {int(value) for value in re.findall(r"\[(\d+)\]", draft.answer)}
    # Excluye enlaces y referencias agrupadas que no pasan la validación de IDs individuales.
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
    """Prepara fragmentos para el modelo con IDs numéricos locales empezando en 1.

    Estos IDs son posiciones en esta lista, no los UUID persistidos de los fragmentos.
    """
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
    """Construye y compila el flujo de una pregunta con dependencias sustituibles.

    Ruta de conocimiento: plan → retrieve → select → generate → verify.
    Un borrador válido pasa a finalize; uno inválido vuelve a generate una vez
    y después pasa a abstain. Las otras intenciones pasan directamente a direct.
    """

    async def plan(state):
        """Clasifica la intención y reescribe la consulta usando el historial.

        Acota query a 1000 caracteres y reinicia el contador de intentos del borrador.
        """
        result = await models.call(
            Plan,
            prompts.PLAN,
            {
                "question": state["question"],
                "history": state["history"],
            },
        )
        # Acota la consulta incluso si el proveedor entrega un texto más largo de lo pedido.
        result.query = result.query.strip()[:1000] or state["question"][:1000]
        return {"plan": result, "attempts": 0, "repair_reason": ""}

    async def retrieve(state):
        """Obtiene candidatos con la consulta autónoma generada por plan."""
        return {"candidates": await retriever.search(state["plan"].query)}

    async def select_evidence(state):
        """Pide al modelo evidencia útil y suficiente y valida sus IDs.

        Si no hay candidatos, la selección es insuficiente o los IDs son inválidos,
        devuelve una lista vacía. La lista útil conserva el orden dado por el modelo
        y se limita a retrieval_top_k fragmentos.
        """
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
        """Genera Draft con evidencia, consulta aclarada, preferencias y motivo de reparación."""
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
        """Valida citas y pide al modelo comprobar el soporte factual del borrador.

        Un borrador que declara información insuficiente pasa a abstención sin
        reparación. Los demás fallos permiten un segundo intento de generación.
        """
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
        """Expone el borrador aceptado y convierte únicamente sus citas en Source."""
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
        """Entrega un mensaje fijo de falta de evidencia, sin exponer el borrador fallido."""
        return {"answer": ABSTAIN, "sources": [], "evidence_status": "insufficient"}

    async def direct(state):
        """Devuelve respuestas fijas para saludo, fuera del dominio y urgencia, sin recuperar."""
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
    # El servicio carga el historial autorizado; el grafo empieza con estado nuevo por pregunta.
    return graph.compile()
