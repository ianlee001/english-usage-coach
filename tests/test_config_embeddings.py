import json
from dataclasses import replace

import httpx
import pytest

from english_coach.config import AppError, Settings, derive_embedding_endpoint
from english_coach.embeddings import DashScopeMultimodalEmbeddings


def test_endpoint_derivation_does_not_send_keys_to_unknown_hosts():
    assert derive_embedding_endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1").endswith(
        "/multimodal-embedding/multimodal-embedding"
    )
    assert derive_embedding_endpoint(
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    ).startswith("https://workspace.")
    assert (
        derive_embedding_endpoint(
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        == ""
    )
    assert derive_embedding_endpoint("https://example.com/compatible-mode/v1") == ""


def test_env_utf8_bom_and_project_relative_paths(tmp_path, monkeypatch):
    for key in ["DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DATA_DIR", "UPLOAD_DIR"]:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=example-secret\nDATA_DIR=mydata\n", encoding="utf-8-sig"
    )
    config = Settings.load(tmp_path)
    assert config.api_key == "example-secret"
    assert config.data_dir == tmp_path / "mydata"
    assert "example-secret" not in repr(config)


def test_embedding_native_payload_batching_and_response_order(settings):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert "input" in body and "contents" in body["input"]
        requests.append(body)
        rows = [
            {"index": i, "type": "text", "embedding": [float(int(row["text"]))] * 8}
            for i, row in enumerate(body["input"]["contents"])
        ]
        return httpx.Response(200, json={"output": {"embeddings": list(reversed(rows))}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        embedding = DashScopeMultimodalEmbeddings(settings, client)
        result = embedding.embed_documents([str(i) for i in range(5)])
    assert [len(r["input"]["contents"]) for r in requests] == [4, 1]
    assert [row[0] for row in result] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 0, "embedding": [1.0]}],
        [{"index": 1, "embedding": [1.0] * 8}],
        [{"index": 0, "type": "image", "embedding": [1.0] * 8}],
    ],
)
def test_embedding_rejects_corrupt_results(settings, rows):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output": {"embeddings": rows}})
        )
    ) as client:
        with pytest.raises(AppError, match="格式"):
            DashScopeMultimodalEmbeddings(settings, client).embed_query("hello")


def test_embedding_error_does_not_expose_response_or_key(settings):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="test-only-key secret"))
    ) as client:
        with pytest.raises(AppError) as error:
            DashScopeMultimodalEmbeddings(settings, client).embed_query("hello")
    assert "401" in str(error.value)
    assert "test-only-key" not in str(error.value)


def test_embedding_validates_empty_and_multibyte_length(settings):
    embedding = DashScopeMultimodalEmbeddings(settings)
    try:
        assert embedding.embed_documents([]) == []
        with pytest.raises(AppError, match="空文本"):
            embedding.embed_query(" ")
        with pytest.raises(AppError, match="过长"):
            embedding.embed_query("中" * 334)
    finally:
        embedding.close()


def test_missing_config_fails_without_http(settings):
    embedding = DashScopeMultimodalEmbeddings(replace(settings, api_key=""))
    try:
        with pytest.raises(AppError, match="DASHSCOPE_API_KEY"):
            embedding.embed_query("hello")
    finally:
        embedding.close()
