from dataclasses import replace

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from streamlit.testing.v1 import AppTest

from english_coach.config import ROOT, AppError
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


def test_streamlit_without_keys_shows_setup_and_can_start_new_chat(settings, monkeypatch):
    config = replace(settings, api_key="", base_url="", embedding_endpoint="")
    monkeypatch.setattr("english_coach.config.Settings.load", lambda: config)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    assert not app.exception
    assert app.chat_input[0].disabled
    assert any(".env.example" in item.value for item in app.info)
    next(button for button in app.button if button.label == "＋ 新建聊天").click().run()
    assert not app.exception
    assert len(app.selectbox[0].options) == 2


def test_streamlit_chat_displays_answer_and_persists(settings, monkeypatch):
    monkeypatch.setattr("english_coach.config.Settings.load", lambda: settings)
    monkeypatch.setattr(
        "english_coach.runtime.init_chat_model",
        lambda **_: ScriptedModel(responses=[AIMessage(content="keep it down：小声一点。")]),
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
    assert not app.exception
    app.chat_input[0].set_value("keep it down 是什么意思？").run()
    assert not app.exception
    assert any("小声一点" in item.value for item in app.markdown)
    app.run()
    assert not app.exception
    assert len(app.chat_message) == 2
