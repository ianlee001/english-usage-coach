import json
from dataclasses import replace

import httpx
from langchain.chat_models import init_chat_model

from english_coach.runtime import Runtime
from english_coach.tools import TimedTavilyAPI, build_tools


def test_qwen_compatible_http_stream_and_tool_roundtrip(settings, monkeypatch):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        assert body["model"] == "qwen3.8-omni-flash"
        assert body["modalities"] == ["text"]
        assert body["stream"] is True
        assert {tool["function"]["name"] for tool in body["tools"]} == {
            "search_materials",
            "web_search",
        }
        if len(requests) == 1:
            deltas = [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "search_materials",
                                "arguments": '{"query":"draw on"}',
                            },
                        }
                    ],
                },
            ]
            finish = "tool_calls"
        else:
            assert any(m["role"] == "tool" for m in body["messages"])
            deltas = [
                {"role": "assistant", "content": "没有上传资料。"},
                {"content": "可以先上传笔记。"},
            ]
            finish = "stop"
        events = []
        for delta in deltas:
            events.append(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
            )
        events.append(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            }
        )
        content = (
            "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=content.encode(), headers={"Content-Type": "text/event-stream"}
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(
            "english_coach.runtime.init_chat_model",
            lambda **kwargs: init_chat_model(**kwargs, http_client=client),
        )
        runtime = Runtime(settings)
        thread = runtime.catalog.new_conversation()
        streamed, statuses = [], []
        answer, _ = runtime.chat(
            thread, "根据笔记解释 draw on", on_text=streamed.append, on_status=statuses.append
        )
        runtime.close()
    assert len(requests) == 2
    assert answer == "没有上传资料。可以先上传笔记。"
    assert "没有上传资料。" in streamed
    assert "正在查阅上传资料…" in statuses


def test_tavily_preserves_sources_and_deduplicates(settings, monkeypatch):
    captured = []

    def results(self, **kwargs):
        captured.append(kwargs)
        return {
            "results": [
                {
                    "url": "https://example.org/usage",
                    "title": "Usage",
                    "content": "A real example.",
                },
                {"url": "javascript:alert(1)", "title": "invalid", "content": "ignore"},
            ]
        }

    monkeypatch.setattr(TimedTavilyAPI, "raw_results", results)
    sources = []
    tools = build_tools(replace(settings, tavily_key="test-tavily"), None, sources, lambda _: None)
    web = next(tool for tool in tools if tool.name == "web_search")
    response = json.loads(web.invoke({"query": "slang current usage"}))
    web.invoke({"query": "same query"})
    assert len(sources) == 1
    assert response["results"][0]["label"] == "网络1"
    assert captured[0]["max_results"] == 5
    assert sources[0]["url"] == "https://example.org/usage"


def test_tavily_sdk_error_is_not_misreported_as_empty_results(settings, monkeypatch):
    def fail(self, **kwargs):
        raise RuntimeError("secret-tavily-key")

    monkeypatch.setattr(TimedTavilyAPI, "raw_results", fail)
    tools = build_tools(replace(settings, tavily_key="test-tavily"), None, [], lambda _: None)
    result = tools[1].invoke({"query": "latest slang"})
    assert "搜索失败" in result
    assert "secret-tavily-key" not in result
