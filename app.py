"""运行方式：uv run streamlit run app.py（不要直接 python app.py）。"""

import streamlit as st

from english_coach.config import AppError, Settings, present, safe_error
from english_coach.runtime import Runtime

st.set_page_config(page_title="English Usage Coach", page_icon="📖", layout="centered")


@st.cache_resource(show_spinner=False)
def get_runtime(settings: Settings) -> Runtime:
    # Streamlit 会重运行脚本；此缓存保留连接，但对话上下文以 SQLite 为准。
    return Runtime(settings)


def show_sources(sources: list[dict]):
    if not sources:
        return
    with st.expander("查看本轮检索来源"):
        st.caption("以下是本轮工具返回的候选来源；是否支持结论，请结合回答与原文判断。")
        for source in sources:
            if source["kind"] == "web":
                st.write(f"[{source['label']}] {source['title']}")
                st.link_button("打开来源", source["url"])
                st.caption(
                    f"检索日期：{source['retrieved_at']}；发布日期：{source.get('published_date') or '来源未提供'}"
                )
            else:
                page = f" · 第 {source['page']} 页" if source.get("page") else ""
                st.write(f"[{source['label']}] {source['filename']}{page}")
                if source.get("section"):
                    st.caption(source["section"])
            # 用纯文本渲染外部片段，避免网页/文档中的 HTML 或图片被主动加载。
            st.text(source["excerpt"])


st.title("English Usage Coach")
st.caption("理解真实英语 · 查近期用法 · 结合你的笔记学习")

try:
    settings = Settings.load()
except AppError as exc:
    st.error(str(exc))
    st.stop()

with st.sidebar:
    st.header("开始学习")
    with st.expander("配置状态", expanded=bool(settings.chat_missing())):
        for name, ready in [
            ("百炼聊天", not settings.chat_missing()),
            ("多模态 Embedding", present(settings.api_key) and bool(settings.embedding_endpoint)),
            ("Tavily 搜索", present(settings.tavily_key)),
            ("MinerU 文档解析", settings.mineru_mode == "flash" or present(settings.mineru_token)),
        ]:
            st.write(f"{'✓' if ready else '○'} {name}")
        st.caption("这里只检查配置是否填写，不代表服务连接已验证。")
        st.caption(f"聊天模型：{settings.llm_model}")
        st.caption(f"资料模型：{settings.embedding_model}")
        if st.button("重新读取配置", use_container_width=True):
            st.rerun()

try:
    runtime = get_runtime(settings)
except Exception as exc:
    st.error(safe_error(exc))
    st.stop()

conversations = runtime.catalog.conversations()
if not conversations:
    runtime.catalog.new_conversation()
    conversations = runtime.catalog.conversations()
ids = [row["id"] for row in conversations]
titles = {row["id"]: row["title"] for row in conversations}
if st.session_state.get("thread_id") not in ids:
    st.session_state.thread_id = ids[0]

with st.sidebar:
    if st.button("＋ 新建聊天", use_container_width=True):
        st.session_state.thread_id = runtime.catalog.new_conversation()
        st.rerun()
    st.selectbox(
        "继续历史聊天", options=ids, format_func=lambda value: titles[value], key="thread_id"
    )
    st.divider()
    st.subheader("我的学习资料")
    st.caption("资料保存在本机，所有聊天均可检索；新建聊天不会删除资料。")
    uploads = st.file_uploader(
        "上传 PDF、DOCX、Markdown 或 TXT",
        type=["pdf", "docx", "md", "txt"],
        accept_multiple_files=True,
    )
    st.caption("PDF/Word 发送到 MinerU 解析；文本片段发送到百炼向量化。只在点击导入时处理。")
    if st.button("导入选中的资料", disabled=not uploads, use_container_width=True):
        progress = st.empty()
        for upload in uploads:
            try:
                result = runtime.ingest(upload.name, upload.getvalue(), progress.info)
                if result["duplicate"]:
                    st.info(f"{result['name']} 已入库，跳过重复处理。")
                else:
                    st.success(f"{result['name']}：已入库 {result['chunks']} 个片段。")
            except Exception as exc:
                st.error(safe_error(exc))
        progress.empty()
    documents = runtime.catalog.documents()
    st.caption(f"已入库 {len(documents)} 份资料")
    for document in documents:
        st.write(f"📄 {document['name']}")
        st.caption(f"{document['chunks']} 个片段")

if settings.chat_missing():
    st.info(
        "首次使用：将项目根目录 .env.example 复制为 .env，填写百炼配置后点击“重新读取配置”。密钥不会在页面显示。"
    )

turns = runtime.catalog.turns(st.session_state.thread_id)
if not turns:
    st.markdown("可以从这些问题开始：")
    st.markdown(
        "- “psych yourself out” 怎么用？\n- “别想太多”怎么说更自然？\n- 这个 slang 最近还流行吗？\n- 根据我上传的笔记，解释 draw on。"
    )
for turn in turns:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        if turn["status"] == "complete":
            st.markdown(turn["answer"])
        else:
            st.error(turn["error"] or "这条请求尚未完成。")
        show_sources(turn["sources"])

question = st.chat_input(
    "输入表达、中文想法，或对资料提问…", disabled=bool(settings.chat_missing()), max_chars=6000
)
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        status = st.empty()
        output = st.empty()
        try:
            with st.spinner("正在处理…"):
                answer, sources = runtime.chat(
                    st.session_state.thread_id,
                    question,
                    on_text=lambda text: output.markdown(text) if text else output.empty(),
                    on_status=status.caption,
                )
            status.empty()
            output.markdown(answer)
            show_sources(sources)
        except Exception as exc:
            output.empty()
            status.empty()
            st.error(safe_error(exc))
