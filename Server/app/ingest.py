"""Herramienta local de ingesta: leer, dividir, vectorizar y actualizar documentos.

Se ejecuta como `python -m app.ingest`. Envía fragmentos al proveedor
de embeddings; no es un endpoint de subida ni implementa roles de usuario.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from langchain_core.documents import Document as LangDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sqlalchemy import delete, select, text

from app.config import EMBEDDING_DIMENSIONS, get_settings
from app.database import Database
from app.models import Chunk, Document
from app.providers import create_embeddings

CHUNK_TOKENS = 600
CHUNK_OVERLAP = 90
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CHUNKS = 2000


def load_pages(path: Path) -> list[LangDocument]:
    """Extrae páginas de un PDF con texto o lee un TXT/Markdown UTF-8.

    Las páginas de PDF se numeran desde 1; los archivos de texto usan page=None.
    Rechaza archivos de más de 20 MiB, PDF cifrados, más de 500 páginas,
    formatos no admitidos y documentos sin texto extraído. No realiza OCR.
    """
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Document exceeds 20 MiB.")
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported.")
        if len(reader.pages) > 500:
            raise ValueError("Document exceeds 500 pages.")
        pages = [
            LangDocument(page_content=page.extract_text() or "", metadata={"page": n})
            for n, page in enumerate(reader.pages, start=1)
        ]
    elif path.suffix.lower() in {".txt", ".md"}:
        pages = [
            LangDocument(page_content=path.read_text(encoding="utf-8"), metadata={"page": None})
        ]
    else:
        raise ValueError("Use a text-based PDF, UTF-8 TXT or Markdown document.")
    pages = [page for page in pages if page.page_content.strip()]
    if not pages:
        raise ValueError("No text extracted. Scanned PDFs require OCR before ingestion.")
    return pages


def split_pages(pages: list[LangDocument]) -> list[LangDocument]:
    """Divide cada página en fragmentos de 600 tokens con solapamiento de 90.

    Preserva los metadatos de página, utiliza cl100k_base y rechaza
    documentos que produzcan más de 2000 fragmentos.
    """
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=CHUNK_TOKENS,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(pages)
    if len(chunks) > MAX_CHUNKS:
        raise ValueError("Document exceeds the 2000-chunk ingestion budget.")
    return chunks


def document_fingerprint(path: Path, signature: str, metadata: dict) -> str:
    """Calcula SHA-256 de contenido, firma de embeddings, división y metadatos.

    La huella permite reconocer una fuente sin cambios antes de pagar
    nuevos embeddings; un cambio de título o confianza también modifica la huella.
    """
    digest = hashlib.sha256(path.read_bytes())
    digest.update(
        json.dumps(
            {
                "embedding": signature,
                "chunk_tokens": CHUNK_TOKENS,
                "overlap": CHUNK_OVERLAP,
                "parser_version": 1,
                **metadata,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    )
    return digest.hexdigest()


async def ingest(
    database,
    embeddings,
    path: Path,
    source_key: str,
    title: str,
    source_url: str | None,
    trust_level: str,
) -> dict:
    """Indexa o reemplaza una fuente identificada por source_key de forma transaccional.

    Calcula embeddings en lotes de 32 antes de modificar el índice activo.
    Después bloquea la source_key y vuelve a comprobar la huella para evitar
    reemplazos concurrentes incoherentes. Una actualización conserva el UUID
    del documento y reemplaza sus fragmentos. Una URL es solo metadato: no se descarga.

    Returns:
        Diccionario con status (indexed o unchanged), document_id y chunks.

    Raises:
        ValueError: Metadatos, documento o vectores no válidos.
        Exception: Los fallos de base o proveedor se propagan; una transacción
            fallida revierte sus cambios.
    """
    if trust_level not in {"demo", "reviewed"}:
        raise ValueError("Invalid trust level.")
    if not title.strip() or len(title) > 300 or not source_key.strip() or len(source_key) > 300:
        raise ValueError("Title and source key must contain 1–300 characters.")
    if source_url and urlparse(source_url).scheme != "https":
        raise ValueError("Source URLs must use HTTPS.")
    await database.check()
    pages = load_pages(path)
    chunks = split_pages(pages)
    fingerprint = document_fingerprint(
        path,
        database.settings.embedding_signature,
        {
            "title": title,
            "url": source_url,
            "trust_level": trust_level,
        },
    )
    async with database.sessions() as session:
        old = await session.scalar(select(Document).where(Document.source_key == source_key))
        if old and old.fingerprint == fingerprint:
            return {"status": "unchanged", "document_id": str(old.id), "chunks": len(chunks)}
    # Obtiene los vectores antes de modificar el índice: un fallo conserva la fuente anterior.
    vectors = []
    for start in range(0, len(chunks), 32):
        batch = chunks[start : start + 32]
        vectors.extend(await embeddings.aembed_documents([chunk.page_content for chunk in batch]))
    if len(vectors) != len(chunks) or any(len(v) != EMBEDDING_DIMENSIONS for v in vectors):
        raise ValueError("Unexpected embedding dimensions or count.")
    async with database.sessions.begin() as session:
        # Serializa ingestas de la misma fuente, también cuando todavía no existe una fila.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 1))"), {"key": source_key}
        )
        old = await session.scalar(select(Document).where(Document.source_key == source_key))
        if old and old.fingerprint == fingerprint:
            return {"status": "unchanged", "document_id": str(old.id), "chunks": len(chunks)}
        identifier = old.id if old else uuid4()
        if old:
            await session.execute(delete(Chunk).where(Chunk.document_id == identifier))
            old.title, old.source_url, old.trust_level = title, source_url, trust_level
            old.fingerprint = fingerprint
        else:
            session.add(
                Document(
                    id=identifier,
                    source_key=source_key,
                    title=title,
                    source_url=source_url,
                    trust_level=trust_level,
                    fingerprint=fingerprint,
                )
            )
            await session.flush()
        session.add_all(
            [
                Chunk(
                    document_id=identifier,
                    content=chunk.page_content,
                    page=chunk.metadata["page"],
                    position=position,
                    embedding=vector,
                )
                for position, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
            ]
        )
    return {"status": "indexed", "document_id": str(identifier), "chunks": len(chunks)}


async def main():
    """Lee argumentos de CLI, exige una clave, ejecuta la ingesta y cierra el pool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--source-key", required=True, help="Stable identifier; same key replaces a source"
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--trust-level", choices=["demo", "reviewed"], default="demo")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.openai_api_key.get_secret_value():
        parser.error("Set OPENAI_API_KEY in the root .env before ingesting.")
    database = Database(settings)
    try:
        result = await ingest(
            database,
            create_embeddings(settings),
            args.path.resolve(),
            args.source_key,
            args.title,
            args.source_url,
            args.trust_level,
        )
        print(json.dumps(result))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
