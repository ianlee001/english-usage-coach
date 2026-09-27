"""上传资料入库：MinerU -> Document -> 分块 -> Embedding -> 本地 Chroma。"""

import hashlib
import json
from pathlib import Path
from typing import Callable

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_mineru import MinerULoader
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from english_coach.config import AppError, Settings, present, safe_error
from english_coach.embeddings import DashScopeMultimodalEmbeddings
from english_coach.storage import Catalog

ALLOWED_SUFFIXES = {".pdf", ".md", ".txt", ".docx"}


def split_documents(docs: list[Document], settings: Settings) -> list[Document]:
    headings = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_bytes,
        chunk_overlap=settings.overlap_bytes,
        length_function=lambda text: len(text.encode("utf-8")),
        separators=["\n\n", "\n", ". ", "。", " ", ""],
    )
    chunks = []
    for doc in docs:
        for section in headings.split_text(doc.page_content):
            section.metadata = {**doc.metadata, **section.metadata}
            chunks.extend(splitter.split_documents([section]))
    return [chunk for chunk in chunks if chunk.page_content.strip()]


class KnowledgeBase:
    def __init__(self, settings: Settings, catalog: Catalog, embeddings=None):
        self.settings = settings
        self.catalog = catalog
        settings.ensure_dirs()
        # 防止更换模型/维度后静默混用旧索引，即使维度恰好一样也不允许。
        fingerprint = {
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
            "chunk_bytes": settings.chunk_bytes,
            "overlap_bytes": settings.overlap_bytes,
            "pipeline_version": 1,
        }
        manifest = settings.data_dir / "index_config.json"
        if manifest.exists():
            if json.loads(manifest.read_text(encoding="utf-8")) != fingerprint:
                raise AppError(
                    "资料索引配置已改变。请恢复原 Embedding/切分配置，或设置新的 DATA_DIR 并重新上传资料；不要混用旧向量。"
                )
        else:
            manifest.write_text(json.dumps(fingerprint, indent=2), encoding="utf-8")
        self.embeddings = embeddings or DashScopeMultimodalEmbeddings(settings)
        self.client = chromadb.PersistentClient(
            path=str(settings.data_dir / "chroma"),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.store = Chroma(
            client=self.client,
            collection_name="english_materials",
            embedding_function=self.embeddings,
            collection_metadata={"hnsw:space": "cosine"},
        )

    def load(self, path: Path, original_name: str) -> list[Document]:
        if path.suffix.lower() in {".txt", ".md"}:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise AppError("TXT/MD 请另存为 UTF-8 编码后上传。") from exc
            return [Document(page_content=text, metadata={"filename": original_name})]
        if self.settings.mineru_mode == "precision" and not present(self.settings.mineru_token):
            raise AppError("PDF/Word 使用 MinerU precision 解析，请先在 .env 填写 MINERU_TOKEN。")
        try:
            loader = MinerULoader(
                source=str(path),
                mode=self.settings.mineru_mode,
                token=self.settings.mineru_token or None,
                language=self.settings.mineru_language,
                timeout=self.settings.mineru_timeout,
                split_pages=path.suffix.lower() == ".pdf",
                **(
                    {"ocr": self.settings.mineru_ocr}
                    if self.settings.mineru_mode == "precision"
                    else {}
                ),
            )
            raw_docs = loader.load()
        except Exception as exc:
            raise AppError(
                f"MinerU 解析失败。请检查令牌、网络、额度或文件格式。错误类型：{type(exc).__name__}。"
            ) from exc
        docs = []
        for doc in raw_docs:
            metadata = {"filename": original_name}
            # MinerULoader split_pages 返回从 1 开始的 PDF 页码。
            if doc.metadata.get("page") is not None:
                metadata["page"] = int(doc.metadata["page"])
            docs.append(Document(page_content=doc.page_content, metadata=metadata))
        return docs

    def ingest(
        self, filename: str, content: bytes, progress: Callable[[str], None] = lambda _: None
    ) -> dict:
        name = Path(filename.replace("\\", "/")).name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise AppError("当前支持 PDF、DOCX、UTF-8 Markdown 和 TXT。")
        if not content:
            raise AppError("文件为空。")
        if len(content) > self.settings.max_upload_mb * 1024 * 1024:
            raise AppError(f"文件超过 {self.settings.max_upload_mb} MB，请拆分后上传。")
        if suffix == ".pdf" and not content.lstrip().startswith(b"%PDF"):
            raise AppError("文件不是有效的 PDF。")
        self.settings.require_embedding()
        doc_id = hashlib.sha256(content).hexdigest()
        if self.catalog.has_document(doc_id):
            return {"duplicate": True, "name": name}
        # 使用内容哈希作为磁盘文件名，避免路径穿越和同名覆盖。
        path = self.settings.upload_dir / f"{doc_id}{suffix}"
        path.write_bytes(content)
        progress("正在读取资料；PDF/Word 由 MinerU 解析…")
        cache_dir = self.settings.data_dir / "parsed"
        cache_dir.mkdir(exist_ok=True)
        cache = cache_dir / f"{doc_id}.json"
        if cache.exists():
            docs = [Document(**item) for item in json.loads(cache.read_text(encoding="utf-8"))]
            for doc in docs:
                doc.metadata["filename"] = name
        else:
            docs = self.load(path, name)
            if not any(doc.page_content.strip() for doc in docs):
                raise AppError("文件未解析出文字。请检查扫描件质量或 MinerU OCR 配置。")
            temp = cache.with_suffix(".tmp")
            temp.write_text(
                json.dumps([doc.model_dump() for doc in docs], ensure_ascii=False), encoding="utf-8"
            )
            temp.replace(cache)
        chunks = split_documents(docs, self.settings)
        if not chunks:
            raise AppError("文件没有可检索的文本内容。")
        ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
        for i, chunk in enumerate(chunks):
            chunk.metadata.update({"doc_id": doc_id, "chunk_id": ids[i]})
        try:
            for start in range(0, len(chunks), 32):
                progress(
                    f"正在向量化并保存资料：{min(start + 32, len(chunks))}/{len(chunks)} 个片段…"
                )
                self.store.add_documents(chunks[start : start + 32], ids=ids[start : start + 32])
            self.catalog.add_document(doc_id, name, path, len(chunks))
        except Exception as exc:
            # 不把失败入库当成成功；删除本次所有确定的片段 ID，可重复上传重试。
            self.store.delete(ids=ids)
            raise AppError(
                f"资料入库未完成：{safe_error(exc)} 解析结果已缓存，可重新上传重试。"
            ) from exc
        return {"duplicate": False, "name": name, "chunks": len(chunks)}

    def search(self, query: str, filename: str = "") -> list[Document]:
        records = self.catalog.documents()
        if not records:
            return []
        self.settings.require_embedding()
        if filename and not any(row["name"] == filename for row in records):
            return []
        return self.store.similarity_search(
            query,
            k=self.settings.top_k,
            **({"filter": {"filename": filename}} if filename else {}),
        )
