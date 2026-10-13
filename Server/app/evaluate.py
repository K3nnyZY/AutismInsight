"""Evaluación opcional del flujo completo con documentos indexados y modelos reales.

Las llamadas son facturables. El reporte contiene métricas y IDs de casos,
no respuestas completas ni una certificación de calidad clínica.
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import Database
from app.graph import build_graph
from app.models import Document
from app.providers import StructuredModels, create_embeddings
from app.retrieval import HybridRetriever


async def evaluate(path: Path, output: Path):
    """Ejecuta secuencialmente entre 1 y 100 casos de un dataset JSONL.

    Valida que las fuentes esperadas estén indexadas, invoca el grafo con
    un timeout por caso y escribe métricas de estado, recuperación de fuentes,
    citas y latencia. No usa ConversationService ni guarda conversaciones.

    Returns:
        True si todos los casos devolvieron un estado permitido por el dataset.

    Los errores de configuración, casos o proveedor interrumpen la evaluación;
    la base siempre se cierra mediante finally.
    """
    settings = get_settings()
    if not settings.openai_api_key.get_secret_value():
        raise ValueError("Set OPENAI_API_KEY; this evaluation makes billable model calls.")
    dataset = await asyncio.to_thread(path.read_text, encoding="utf-8")
    cases = [json.loads(line) for line in dataset.splitlines() if line.strip()]
    if not cases or len(cases) > 100:
        raise ValueError("Use 1–100 cases per evaluation run.")
    database = Database(settings)
    try:
        await database.check()
        async with database.sessions() as session:
            source_keys = dict(
                (await session.execute(select(Document.id, Document.source_key))).all()
            )
        missing = {key for case in cases for key in case["expected_source_keys"]} - set(
            source_keys.values()
        )
        if missing:
            raise ValueError(f"Index expected sources first: {', '.join(sorted(missing))}")
        graph = build_graph(
            StructuredModels(settings),
            HybridRetriever(database, create_embeddings(settings), settings),
            settings,
        )
        rows = []
        for case in cases:
            started = time.monotonic()
            async with asyncio.timeout(settings.chat_timeout_seconds):
                state = await graph.ainvoke(
                    {
                        "question": case["question"],
                        "history": case.get("history", []),
                        "audience": "person",
                        "style": "standard",
                    },
                    config={"recursion_limit": 20},
                )
            candidate_keys = {
                source_keys.get(item.document_id) for item in state.get("candidates", [])
            }
            expected = set(case["expected_source_keys"])
            rows.append(
                {
                    "id": case["id"],
                    "status": state["evidence_status"],
                    "status_pass": state["evidence_status"] in case["expected_status"],
                    "retrieval_source_recall": len(expected & candidate_keys) / len(expected)
                    if expected
                    else None,
                    "citation_count": len(state["sources"]),
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }
            )
        latencies = sorted(row["latency_ms"] for row in rows)
        report = {
            "chat_model": settings.chat_model,
            "embedding_signature": settings.embedding_signature,
            "cases": rows,
            "status_pass_rate": sum(row["status_pass"] for row in rows) / len(rows),
            "latency_median_ms": statistics.median(latencies),
            "latency_p95_ms": latencies[max(0, int(len(latencies) * 0.95 + 0.999) - 1)],
            "note": "Smoke metrics; clinical suitability requires independent human review.",
        }
        await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            output.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Status pass rate: {report['status_pass_rate']:.0%}; report: {output}")
        return report["status_pass_rate"] == 1
    finally:
        await database.close()


def main():
    """Lee dataset y ruta de reporte; termina con código 0 si pasa y 1 si falla."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, default=Path("../artifacts/evaluation.json"))
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(evaluate(args.dataset, args.output)) else 1)


if __name__ == "__main__":
    main()
