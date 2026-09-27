import hashlib
from dataclasses import replace

import pytest
from langchain_core.embeddings import Embeddings

from english_coach.config import Settings


class LocalEmbeddings(Embeddings):
    """仅用于离线测试；绝不用于产品生成答案或真实检索。"""

    calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [
            [(byte - 127) / 128 for byte in hashlib.sha256(text.encode()).digest()[:8]]
            for text in texts
        ]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.fixture
def settings(tmp_path):
    return replace(
        Settings(),
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
        api_key="test-only-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_endpoint="https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
        embedding_dimensions=8,
    )
