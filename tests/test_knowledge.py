from dataclasses import replace

import pytest
from langchain_core.documents import Document

from english_coach.config import AppError
from english_coach.knowledge import KnowledgeBase, split_documents
from english_coach.storage import Catalog
from tests.conftest import LocalEmbeddings


def test_split_preserves_source_and_byte_limit(settings):
    docs = [
        Document(
            page_content="# psych yourself out\n" + "中英文 example 使用场景。" * 200,
            metadata={"filename": "notes.pdf", "page": 2},
        )
    ]
    chunks = split_documents(docs, settings)
    assert len(chunks) > 2
    assert all(len(c.page_content.encode("utf-8")) <= settings.chunk_bytes for c in chunks)
    assert all(c.metadata["page"] == 2 and c.metadata["filename"] == "notes.pdf" for c in chunks)
    assert all(c.metadata["h1"] == "psych yourself out" for c in chunks)


def make_kb(settings):
    catalog = Catalog(settings.data_dir / "catalog.sqlite")
    embeddings = LocalEmbeddings()
    return KnowledgeBase(settings, catalog, embeddings), embeddings


def test_ingest_deduplicates_and_persists(settings):
    kb, embeddings = make_kb(settings)
    content = b"# draw on\nUse knowledge or experience."
    result = kb.ingest("../../notes.md", content)
    assert result["name"] == "notes.md"
    calls = embeddings.calls
    assert kb.ingest("notes-again.md", content)["duplicate"] is True
    assert embeddings.calls == calls
    kb2, _ = make_kb(settings)
    results = kb2.search("draw on", "notes.md")
    assert results and results[0].metadata["filename"] == "notes.md"
    assert kb2.search("draw on", "missing.pdf") == []
    assert len(kb2.catalog.documents()) == 1


def test_partial_ingest_is_rolled_back_and_retry_succeeds(settings, monkeypatch):
    kb, _ = make_kb(settings)
    original = kb.store.add_documents

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(kb.store, "add_documents", fail_after_write)
    with pytest.raises(AppError, match="入库未完成"):
        kb.ingest("notes.md", b"# keep it down\nPlease be quiet.")
    assert kb.store.get()["ids"] == []
    assert kb.catalog.documents() == []
    monkeypatch.setattr(kb.store, "add_documents", original)
    assert kb.ingest("notes.md", b"# keep it down\nPlease be quiet.")["chunks"] == 1


def test_index_refuses_different_model_even_same_dimension(settings):
    kb, _ = make_kb(settings)
    with pytest.raises(AppError, match="配置已改变"):
        KnowledgeBase(
            replace(settings, embedding_model="different-model"), kb.catalog, LocalEmbeddings()
        )


def test_mineru_uses_precision_token_and_preserves_pages(settings, monkeypatch, tmp_path):
    kwargs_seen = {}

    class StubLoader:
        def __init__(self, **kwargs):
            kwargs_seen.update(kwargs)

        def load(self):
            return [Document(page_content="# Expression\nEnglish notes", metadata={"page": 3})]

    monkeypatch.setattr("english_coach.knowledge.MinerULoader", StubLoader)
    settings = replace(settings, mineru_token="test-mineru")
    kb, _ = make_kb(settings)
    docs = kb.load(tmp_path / "test.pdf", "lesson.pdf")
    assert kwargs_seen["mode"] == "precision" and kwargs_seen["ocr"] is True
    assert kwargs_seen["token"] == "test-mineru" and kwargs_seen["split_pages"] is True
    assert docs[0].metadata == {"filename": "lesson.pdf", "page": 3}


def test_invalid_uploads_are_not_indexed(settings):
    kb, _ = make_kb(settings)
    for name, content in [("bad.pdf", b"not a PDF"), ("empty.md", b""), ("file.exe", b"content")]:
        with pytest.raises(AppError):
            kb.ingest(name, content)
    assert kb.catalog.documents() == []
