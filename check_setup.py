"""离线检查：不发起 API 调用，不显示密钥。uv run python check_setup.py"""

import sys
from importlib.metadata import version

from english_coach.config import AppError, Settings, present


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info[:2] != (3, 13):
        print("本项目需要 Python 3.13，请使用项目 .venv 解释器。")
        return 1
    for package in [
        "fastapi",
        "uvicorn",
        "youtube-transcript-api",
        "langchain",
        "langchain-openai",
        "langchain-chroma",
        "langchain-mineru",
        "langgraph-checkpoint-sqlite",
    ]:
        print(f"{package}: {version(package)}")
    try:
        settings = Settings.load()
    except AppError as exc:
        print(f"配置错误：{exc}")
        return 1
    checks = {
        "DASHSCOPE_API_KEY": present(settings.api_key),
        "DASHSCOPE_BASE_URL": present(settings.base_url),
        "Embedding 原生接口地址": present(settings.embedding_endpoint),
        "TAVILY_API_KEY": present(settings.tavily_key),
        "MINERU_TOKEN": settings.mineru_mode == "flash" or present(settings.mineru_token),
    }
    for name, ok in checks.items():
        print(f"{'已填写' if ok else '未填写'}: {name}")
    print(f"聊天模型：{settings.llm_model}")
    print(f"Embedding 模型：{settings.embedding_model} / {settings.embedding_dimensions} 维")
    print("此检查不联网；已填写不等于凭证或服务可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
