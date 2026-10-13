from collections import deque
from uuid import uuid4

import pytest

from app.config import Settings
from app.graph import Draft, Plan, Selection, Verification, build_graph, citation_error
from app.retrieval import Evidence, reciprocal_rank_fusion


class ScriptedModels:
    def __init__(self, results):
        self.results = deque(results)
        self.calls = []

    async def call(self, schema, system, payload, role="planner"):
        self.calls.append((schema, payload))
        result = self.results.popleft()
        assert isinstance(result, schema)
        return result


class Retriever:
    def __init__(self, documents):
        self.documents = documents
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return self.documents


def fragment(content="El documento describe dificultades en la comunicación."):
    return Evidence(uuid4(), uuid4(), content, "Material de prueba", 1, None, "demo")


def initial(**kwargs):
    return {
        "question": "¿Qué dice sobre comunicación?",
        "history": [],
        "audience": "person",
        "style": "simple",
        **kwargs,
    }


async def run(results, documents=None, **kwargs):
    models = ScriptedModels(results)
    retriever = Retriever(documents if documents is not None else [fragment()])
    graph = build_graph(models, retriever, Settings(_env_file=None))
    state = await graph.ainvoke(initial(**kwargs), config={"recursion_limit": 20})
    assert not models.results
    return state, models, retriever


async def test_grounded_answer_returns_only_cited_sources():
    state, _, _ = await run(
        [
            Plan(route="knowledge", query="comunicación"),
            Selection(ids=[2, 1], sufficient=True),
            Draft(
                answer="Describe dificultades en la comunicación [2].",
                citations=[2],
                insufficient_evidence=False,
            ),
            Verification(supported=True, reason=""),
        ],
        [fragment(), fragment("Otro fragmento.")],
    )
    assert state["evidence_status"] == "grounded"
    assert [s.citation for s in state["sources"]] == [2]
    assert state["sources"][0].trust_level == "demo"
    assert state["sources"][0].page == 1


async def test_empty_index_abstains_without_generating():
    state, models, _ = await run([Plan(route="knowledge", query="autismo")], [])
    assert state["evidence_status"] == "insufficient"
    assert state["sources"] == []
    assert len(models.calls) == 1


@pytest.mark.parametrize(
    "selection",
    [
        Selection(ids=[1], sufficient=False),
        Selection(ids=[99], sufficient=True),
        Selection(ids=[], sufficient=True),
    ],
)
async def test_irrelevant_or_invalid_evidence_abstains(selection):
    state, _, _ = await run([Plan(route="knowledge", query="autismo"), selection])
    assert state["evidence_status"] == "insufficient"


async def test_invalid_citation_repairs_once():
    state, models, _ = await run(
        [
            Plan(route="knowledge", query="comunicación"),
            Selection(ids=[1], sufficient=True),
            Draft(answer="Afirmación [99].", citations=[99], insufficient_evidence=False),
            Draft(answer="Describe dificultades [1].", citations=[1], insufficient_evidence=False),
            Verification(supported=True, reason=""),
        ]
    )
    assert state["attempts"] == 2
    assert state["evidence_status"] == "grounded"
    assert "citas" in models.calls[-2][1]["repair_reason"]


async def test_unsupported_claim_is_not_exposed_after_bounded_repair():
    bad = Draft(answer="Promete una cura [1].", citations=[1], insufficient_evidence=False)
    state, _, _ = await run(
        [
            Plan(route="knowledge", query="cura"),
            Selection(ids=[1], sufficient=True),
            bad,
            Verification(supported=False, reason="No hay respaldo"),
            bad,
            Verification(supported=False, reason="No hay respaldo"),
        ]
    )
    assert state["evidence_status"] == "insufficient"
    assert "cura" not in state["answer"]
    assert state["sources"] == []


async def test_followup_retrieves_rewritten_query_and_passes_history():
    history = [{"role": "user", "content": "Quiero saber sobre la comunicación."}]
    _, models, retriever = await run(
        [
            Plan(route="knowledge", query="comunicación en autismo"),
            Selection(ids=[], sufficient=False),
        ],
        question="¿Y eso?",
        history=history,
    )
    assert models.calls[0][1]["history"] == history
    assert retriever.queries == ["comunicación en autismo"]


async def test_followup_answer_and_verifier_receive_resolved_question():
    _, models, _ = await run(
        [
            Plan(route="knowledge", query="comunicación en autismo"),
            Selection(ids=[1], sufficient=True),
            Draft(
                answer="El documento describe dificultades [1].",
                citations=[1],
                insufficient_evidence=False,
            ),
            Verification(supported=True, reason=""),
        ],
        question="¿Y eso?",
    )
    assert models.calls[-2][1]["resolved_query"] == "comunicación en autismo"
    assert models.calls[-1][1]["resolved_query"] == "comunicación en autismo"


@pytest.mark.parametrize("route", ["conversation", "urgent", "out_of_scope"])
async def test_nonknowledge_routes_do_not_retrieve(route):
    state, _, retriever = await run([Plan(route=route, query="")])
    assert state["evidence_status"] == "not_applicable"
    assert retriever.queries == []


@pytest.mark.parametrize(
    "answer,citations",
    [
        ("A [2]", [2]),
        ("A [1]", [2]),
        ("A", [1]),
        ("A [0]", [0]),
        ("A [1,2]", [1]),
        ("A [1] https://inventado.test", [1]),
    ],
)
def test_invalid_citation_formats(answer, citations):
    assert citation_error(Draft(answer=answer, citations=citations, insufficient_evidence=False), 1)


def test_rrf_rewards_both_rankings_and_removes_duplicate_text():
    first, shared, duplicate = fragment("uno"), fragment("dos"), fragment("dos")
    result = reciprocal_rank_fusion([[first, shared], [shared, duplicate]], 6)
    assert result[0].chunk_id == shared.chunk_id
    assert len(result) == 2
