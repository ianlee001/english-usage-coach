# 版本记录

## v0.2.0 — 第二版（2026-10-07）

从 Streamlit 迁移到 FastAPI REST 后端和原生 HTML/CSS/JavaScript 前端，新增 Chrome 原生侧栏的视频伴学，让视频中的难点进入可复习的单词本。

### 新增与调整

- 保留英语对话、流式回答、SQLite 历史、MinerU 文档解析和 Chroma RAG。
- 新增 YouTube 英文字幕获取：优先人工字幕，其次自动字幕；提供英文 SRT/VTT 导入后备入口。
- 根据英语水平分析当前及下一分钟字幕，只在播放到难点时显示解释卡片。
- 只将实际播放时显示的难点保存到单词本；支持收藏、掌握、搜索、视频出处和继续请教。
- 聊天 Agent 新增单词本检索、视频语境查询工具。
- 主网页和侧栏使用浅蓝配色、圆角卡片、扁平化视觉与响应式布局。
- 本地网页使用同源 cookie，扩展使用独立连接码；API Key 保留在后端。
- 增加真实视频侧栏与单词本演示，保留第一版的 8 张演示图片。

### 从第一版升级

1. 停止旧服务，备份自己的 `.env`、`data/` 和 `uploads/`。
2. 更新代码后，在项目根目录执行 `uv sync --locked --python 3.13`。
3. 保留原 `.env`、数据目录和 Embedding 配置，不要用模板覆盖真实配置。
4. 运行 `uv run --locked python -m backend.server`，访问 `http://127.0.0.1:8000`。
5. 首次启动会备份旧聊天 SQLite 数据库并增量建表；已有 Chroma 索引继续使用。
6. 按 README 加载并配对 Chrome 扩展；更新扩展后重新加载并刷新 YouTube 页面。

旧命令 `streamlit run app.py` 不再适用。扩展安装后也需要本地后端持续运行。

### 验证与边界

35 项 Python 测试、2 项 Node 扩展纯逻辑测试，以及 Ruff 检查和格式检查通过。真实演示与验证边界见 [docs/demo.md](docs/demo.md) 和 [docs/verification.md](docs/verification.md)。

本版用于本机单用户、单进程。仅支持有可用英文字幕的 YouTube 普通视频；不包含直播、Shorts、无字幕语音识别、视频画面理解或浏览器商店发布。字幕获取受网络和平台接口变化影响，模型判断的难度和释义也可能不准确。

## 第一版 — Streamlit 初始版本（2026-09-28）

初始提交：`050c63e`。提供英语学习对话、联网检索、资料 RAG 和持久化聊天历史。

[第一版演示](docs/demo-v1.md) · [第一版使用说明存档](docs/streamlit_readme.md)
