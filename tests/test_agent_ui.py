import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from english_coach.config import AppError
from english_coach.runtime import Runtime


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage] = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-scripted-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if not self.responses:
            raise RuntimeError("test-only-key must not leak")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def test_agent_tool_loop_and_sqlite_context_survive_restart(settings):
    runtime = Runtime(settings)
    thread = runtime.catalog.new_conversation()
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_materials",
                        "args": {"query": "draw on"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="上传资料中没有找到相关内容。"),
        ]
    )
    answer, _ = runtime.chat(thread, "按笔记解释 draw on", model=model)
    assert "没有找到" in answer
    assert any(m.type == "tool" for m in model.seen[-1])
    runtime.close()
    restarted = Runtime(settings)
    followup = ScriptedModel(responses=[AIMessage(content="这里是另一个例句。")])
    restarted.chat(thread, "再给一个例句", model=followup)
    users = [m.content for m in followup.seen[0] if isinstance(m, HumanMessage)]
    assert users == ["按笔记解释 draw on", "再给一个例句"]
    assert len(restarted.catalog.turns(thread)) == 2
    other = restarted.catalog.new_conversation()
    fresh = ScriptedModel(responses=[AIMessage(content="新对话")])
    restarted.chat(other, "你好", model=fresh)
    assert [m.content for m in fresh.seen[0] if isinstance(m, HumanMessage)] == ["你好"]
    restarted.close()


def test_failed_turn_is_not_replayed_and_error_is_redacted(settings):
    runtime = Runtime(settings)
    thread = runtime.catalog.new_conversation()
    runtime.chat(
        thread, "之前的问题", model=ScriptedModel(responses=[AIMessage(content="之前的回答")])
    )
    with pytest.raises(AppError) as error:
        runtime.chat(thread, "失败的问题", model=ScriptedModel())
    assert "test-only-key" not in str(error.value)
    assert runtime.catalog.turns(thread)[-1]["status"] == "failed"
    next_model = ScriptedModel(responses=[AIMessage(content="恢复正常")])
    runtime.chat(thread, "继续", model=next_model)
    assert [m.content for m in next_model.seen[0] if isinstance(m, HumanMessage)] == [
        "之前的问题",
        "继续",
    ]
    runtime.close()


def test_agent_call_budget_stops_repeated_tool_loop(settings):
    runtime = Runtime(settings)
    thread = runtime.catalog.new_conversation()
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_materials",
                        "args": {"query": "x"},
                        "id": f"call_{index}",
                        "type": "tool_call",
                    }
                ],
            )
            for index in range(10)
        ]
    )
    with pytest.raises(AppError):
        runtime.chat(thread, "反复查询", model=model)
    assert len(model.seen) == 6
    assert runtime.catalog.turns(thread)[0]["status"] == "failed"
    runtime.close()
