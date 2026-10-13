"""Recuperación híbrida del corpus: vectores, palabras clave y fusión por posiciones."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, literal_column, select, text

from app.config import Settings
from app.database import Database
from app.models import Chunk, Document


@dataclass(frozen=True)
class Evidence:
    """Fragmento inmutable con texto y procedencia que el grafo puede citar."""

    chunk_id: UUID
    document_id: UUID
    content: str
    title: str
    page: int | None
    url: str | None
    trust_level: str


def reciprocal_rank_fusion(rankings: list[list[Evidence]], limit: int) -> list[Evidence]:
    """Combina listas ordenadas usando suma de 1 / (60 + posición).

    rankings contiene los resultados semánticos y léxicos. Una coincidencia
    en ambas listas recibe más peso; limit acota la salida. Se eliminan IDs
    repetidos por lista y textos exactamente iguales en el resultado final.
    Las puntuaciones combinan posiciones, no probabilidades de certeza.
    """
    scores: dict[UUID, float] = {}
    items: dict[UUID, Evidence] = {}
    for ranking in rankings:
        seen: set[UUID] = set()
        for rank, item in enumerate(ranking, start=1):
            if item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0) + 1 / (60 + rank)
            items[item.chunk_id] = item
    selected: list[Evidence] = []
    contents: set[str] = set()
    for chunk_id in sorted(scores, key=lambda key: (-scores[key], str(key))):
        item = items[chunk_id]
        if item.content not in contents:
            selected.append(item)
            contents.add(item.content)
        if len(selected) >= limit:
            break
    return selected


class HybridRetriever:
    """Consulta pgvector y texto completo respetando el nivel de revisión permitido."""

    def __init__(self, database: Database, embeddings, settings: Settings):
        """Recibe base, proveedor de embeddings y límites de recuperación."""
        self.database = database
        self.embeddings = embeddings
        self.settings = settings

    async def search(self, query: str) -> list[Evidence]:
        """Vectoriza query y fusiona dos búsquedas de candidatos en PostgreSQL.

        Una búsqueda ordena por distancia coseno; la otra utiliza texto completo
        en español. Cada una devuelve como máximo retrieval_candidates filas;
        RRF reduce la combinación al mismo máximo. La selección por modelo
        y la comprobación de suficiencia se realizan después, en graph.py.
        """
        vector = await self.embeddings.aembed_query(query)
        allowed = ["reviewed", "demo"] if self.settings.demo_sources_enabled else ["reviewed"]
        base = (
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.trust_level.in_(allowed))
        )
        lexical_query = func.websearch_to_tsquery(literal_column("'spanish'::regconfig"), query)
        async with self.database.sessions() as session:
            # La exploración iterativa busca más vecinos si el filtro de confianza descarta algunos.
            await session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            semantic = await session.execute(
                base.order_by(Chunk.embedding.cosine_distance(vector)).limit(
                    self.settings.retrieval_candidates
                )
            )
            lexical = await session.execute(
                base.where(Chunk.search_vector.op("@@")(lexical_query))
                .order_by(func.ts_rank_cd(Chunk.search_vector, lexical_query).desc())
                .limit(self.settings.retrieval_candidates)
            )

        def evidence(rows):
            """Convierte pares ORM (Chunk, Document) en Evidence con su página y procedencia."""
            return [
                Evidence(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    content=chunk.content,
                    title=document.title,
                    page=chunk.page,
                    url=document.source_url,
                    trust_level=document.trust_level,
                )
                for chunk, document in rows
            ]

        return reciprocal_rank_fusion(
            [evidence(semantic), evidence(lexical)], self.settings.retrieval_candidates
        )
