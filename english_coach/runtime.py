"""连接 UI、Agent、工具和持久化。单用户 MVP 用锁避免同一进程并发写入。"""

import atexit
import sqlite3
import threading
from typing import Callable

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer

from english_coach.config import AppError, Settings, safe_error
from english_coach.knowledge import KnowledgeBase
from english_coach.prompts import system_prompt
from english_coach.storage import Catalog
from english_coach.tools import build_tools


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
        )
    return ""


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.lock = threading.RLock()
        self.catalog = Catalog(settings.data_dir / "catalog.sqlite")
        self.knowledge = KnowledgeBase(settings, self.catalog)
        self.connection = sqlite3.connect(
            settings.data_dir / "checkpoints.sqlite", check_same_thread=False, timeout=30
        )
        self.checkpointer = SqliteSaver(self.connection)
        self.checkpointer.setup()
        atexit.register(self.close)

    def close(self):
        with self.lock:
            self.connection.close()
            if hasattr(self.knowledge.embeddings, "close"):
                self.knowledge.embeddings.close()

    def restore_completed_context(self, agent, thread_id: str):
        """失败/中断后去掉半截工具调用；重建已完成对话，不重复调用外部服务。"""
        self.checkpointer.delete_thread(thread_id)
        messages = []
        for row in self.catalog.turns(thread_id):
            if row["status"] == "complete":
                messages.extend(
                    [HumanMessage(content=row["question"]), AIMessage(content=row["answer"])]
                )
        if messages:
            agent.update_state({"configurable": {"thread_id": thread_id}}, {"messages": messages})

    def chat(
        self,
        thread_id: str,
        question: str,
        on_text: Callable[[str], None] = lambda _: None,
        on_status: Callable[[str], None] = lambda _: None,
        model=None,
    ) -> tuple[str, list[dict]]:
        question = question.strip()
        if not question:
            raise AppError("请输入问题。")
        if len(question) > 6000:
            raise AppError("本轮输入过长，请缩短到 6000 个字符以内，长资料请通过上传导入。")
        if model is None and self.settings.chat_missing():
            raise AppError("请先在项目根目录 .env 中填写 DASHSCOPE_API_KEY 和 DASHSCOPE_BASE_URL。")
        with self.lock:
            sources: list[dict] = []
            if model is None:
                model = init_chat_model(
                    model=self.settings.llm_model,
                    model_provider="openai",
                    api_key=self.settings.api_key,
                    base_url=self.settings.base_url,
                    temperature=0.4,
                    max_tokens=3000,
                    timeout=self.settings.timeout,
                    max_retries=1,
                    streaming=True,
                    stream_usage=False,
                    model_kwargs={"modalities": ["text"]},
                )

            def emit_tool_status(label: str):
                # 工具可能在线程池运行：通过 LangGraph 事件回到 UI 主线程。
                get_stream_writer()({"status": label})

            agent = create_agent(
                model=model,
                tools=build_tools(self.settings, self.knowledge, sources, emit_tool_status),
                system_prompt=system_prompt([row["name"] for row in self.catalog.documents()]),
                checkpointer=self.checkpointer,
            )
            history = self.catalog.turns(thread_id)
            if history and history[-1]["status"] != "complete":
                self.restore_completed_context(agent, thread_id)
            turn_id = self.catalog.begin_turn(thread_id, question)
            buffer = ""
            final = ""
            on_status("正在组织回答…")
            try:
                # 每轮只提交一条新增消息；不要重复把 UI 历史再次塞给 checkpointer。
                events = agent.stream(
                    {"messages": [HumanMessage(content=question, id=turn_id)]},
                    {"configurable": {"thread_id": thread_id}, "recursion_limit": 16},
                    stream_mode=["messages", "updates", "custom"],
                )
                for mode, payload in events:
                    if mode == "custom":
                        if isinstance(payload, dict) and payload.get("status"):
                            on_status(payload["status"])
                    elif mode == "messages":
                        chunk, metadata = payload
                        if (
                            isinstance(chunk, AIMessageChunk)
                            and metadata.get("langgraph_node") == "model"
                        ):
                            fragment = text_content(chunk.content)
                            if fragment:
                                buffer += fragment
                                on_text(buffer)
                    elif mode == "updates":
                        for update in payload.values():
                            if not isinstance(update, dict):
                                continue
                            for message in update.get("messages", []):
                                if isinstance(message, AIMessage):
                                    if message.tool_calls:
                                        buffer = ""
                                        on_text("")
                                    elif text_content(message.content):
                                        final = text_content(message.content)
                                        buffer = ""
                if not final:
                    raise AppError("模型没有返回可展示的最终回答，请重试。")
                self.catalog.finish_turn(turn_id, final, sources)
                on_text(final)
                on_status("回答完成")
                return final, sources
            except Exception as exc:
                error = safe_error(exc)
                self.catalog.finish_turn(turn_id, "", sources, error=error)
                # 清理部分工具状态；下次调用也会按失败标记再次恢复。
                try:
                    self.restore_completed_context(agent, thread_id)
                except Exception:
                    pass
                raise AppError(error) from exc

    def ingest(self, name: str, data: bytes, progress=lambda _: None):
        with self.lock:
            return self.knowledge.ingest(name, data, progress)
