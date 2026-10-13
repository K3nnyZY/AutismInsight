from pathlib import Path

import pytest
import tiktoken
from langchain_core.documents import Document

from app.ingest import CHUNK_TOKENS, document_fingerprint, load_pages, split_pages
from app.models import Message
from app.service import bounded_history


def test_pdf_is_readable_and_preserves_pages():
    pages = load_pages(Path(__file__).resolve().parents[1] / "app" / "Asperger.pdf")
    assert pages and pages[0].metadata["page"] == 1
    assert pages[0].page_content.strip()


def test_chunk_budget_and_page_metadata():
    chunks = split_pages(
        [Document(page_content="Comunicación y accesibilidad. " * 2000, metadata={"page": 7})]
    )
    encoder = tiktoken.get_encoding("cl100k_base")
    assert len(chunks) > 1
    assert all(len(encoder.encode(chunk.page_content)) <= CHUNK_TOKENS for chunk in chunks)
    assert all(chunk.metadata["page"] == 7 for chunk in chunks)


def test_document_fingerprint_includes_metadata_and_model(tmp_path):
    path = tmp_path / "document.md"
    path.write_text("Contenido", encoding="utf-8")
    first = document_fingerprint(path, "model:1536", {"trust_level": "demo"})
    assert first == document_fingerprint(path, "model:1536", {"trust_level": "demo"})
    assert first != document_fingerprint(path, "model:1536", {"trust_level": "reviewed"})
    assert first != document_fingerprint(path, "new-model:1536", {"trust_level": "demo"})


def test_empty_document_rejected(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("   ")
    with pytest.raises(ValueError, match="No text"):
        load_pages(path)


def test_history_is_recent_and_bounded():
    messages = [
        Message(role="user", content="old" * 100),
        Message(role="assistant", content="recent"),
        Message(role="user", content="new"),
    ]
    assert bounded_history(messages, 10) == [
        {"role": "assistant", "content": "recent"},
        {"role": "user", "content": "new"},
    ]
