"""把百炼原生多模态 Embedding API 适配为 Chroma 所需的文本接口。"""

import math
import time

import httpx
from langchain_core.embeddings import Embeddings

from english_coach.config import AppError, Settings


class DashScopeMultimodalEmbeddings(Embeddings):
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        # 禁止跨域重定向，避免 Authorization 被意外转发。
        self.client = client or httpx.Client(timeout=settings.timeout, follow_redirects=False)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.settings.require_embedding()
        for text in texts:
            if not text.strip():
                raise AppError("不能向量化空文本。")
            # byte 上界比字符数保守，避免把中文误当成少量 token。
            if len(text.encode("utf-8")) > 1000:
                raise AppError("检索文本过长，请缩短查询；资料片段应先切分到 1000 UTF-8 字节以内。")
        vectors = []
        # 小批次，避免多模态接口的每批输入上限；入库失败可安全重试。
        for start in range(0, len(texts), 4):
            batch = texts[start : start + 4]
            body = {
                "model": self.settings.embedding_model,
                "input": {"contents": [{"text": text} for text in batch]},
            }
            for attempt in range(3):
                try:
                    response = self.client.post(
                        self.settings.embedding_endpoint,
                        json=body,
                        headers={"Authorization": f"Bearer {self.settings.api_key}"},
                    )
                except httpx.TransportError as exc:
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise AppError(
                        "Embedding 网络连接失败，请检查原生接口地址或稍后重试。"
                    ) from exc
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if response.status_code != 200:
                    raise AppError(
                        f"Embedding 请求失败（HTTP {response.status_code}），请检查原生接口地址、模型权限、额度和输入长度。"
                    )
                break
            try:
                rows = response.json()["output"]["embeddings"]
                if len(rows) != len(batch) or {r["index"] for r in rows} != set(range(len(batch))):
                    raise ValueError("indices")
                rows = sorted(rows, key=lambda row: row["index"])
                for row in rows:
                    vector = row["embedding"]
                    if (
                        row.get("type", "text") != "text"
                        or len(vector) != self.settings.embedding_dimensions
                    ):
                        raise ValueError("dimensions")
                    if not all(isinstance(n, (float, int)) and math.isfinite(n) for n in vector):
                        raise ValueError("non-finite")
                    vectors.append([float(n) for n in vector])
            except (KeyError, TypeError, ValueError) as exc:
                raise AppError(
                    "Embedding 响应格式、数量或向量维度不匹配，请核对所选模型与 EMBEDDING_DIMENSIONS。"
                ) from exc
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def close(self):
        self.client.close()
