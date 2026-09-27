"""读取配置但不打印密钥；所有相对路径都相对于项目根目录。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
EMBEDDING_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


class AppError(Exception):
    """可以直接向用户展示的、已排除密钥的错误。"""


def present(value: str) -> bool:
    return bool(value.strip()) and not value.lower().startswith(("replace_", "your_", "<"))


def derive_embedding_endpoint(base_url: str) -> str:
    """仅为已知百炼主机推导原生地址；代理/套餐地址必须显式配置。"""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    official = host in {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    } or host.endswith(".maas.aliyuncs.com")
    if not official or host.startswith(("token-plan.", "coding.")):
        return ""
    return urlunsplit((parts.scheme, parts.netloc, EMBEDDING_PATH, "", ""))


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    llm_model: str = "qwen3.8-omni-flash"
    embedding_model: str = "tongyi-embedding-vision-flash"
    embedding_endpoint: str = ""
    embedding_dimensions: int = 768
    tavily_key: str = field(default="", repr=False)
    mineru_token: str = field(default="", repr=False)
    mineru_mode: str = "precision"
    mineru_language: str = "en"
    mineru_ocr: bool = True
    data_dir: Path = ROOT / "data"
    upload_dir: Path = ROOT / "uploads"
    max_upload_mb: int = 20
    top_k: int = 5
    chunk_bytes: int = 900
    overlap_bytes: int = 120
    timeout: int = 90
    mineru_timeout: int = 300

    @classmethod
    def load(cls, root: Path = ROOT) -> "Settings":
        # 环境变量优先于 .env；每次页面 rerun 重新读取，修改后无需泄露配置。
        values = {**dotenv_values(root / ".env", encoding="utf-8-sig"), **os.environ}

        def get(name, default=""):
            return str(values.get(name) or default).strip()

        def number(name, default, low, high):
            try:
                value = int(get(name, str(default)))
            except ValueError as exc:
                raise AppError(f"{name} 必须是整数。") from exc
            if not low <= value <= high:
                raise AppError(f"{name} 必须在 {low}–{high} 之间。")
            return value

        base_url = get("DASHSCOPE_BASE_URL").rstrip("/")
        endpoint = get("DASHSCOPE_EMBEDDING_ENDPOINT") or derive_embedding_endpoint(base_url)
        for name, url in [
            ("DASHSCOPE_BASE_URL", base_url),
            ("DASHSCOPE_EMBEDDING_ENDPOINT", endpoint),
        ]:
            if url and present(url):
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.netloc
                    or parsed.username
                    or parsed.query
                    or parsed.fragment
                ):
                    raise AppError(f"{name} 应为有效 HTTP(S) 地址，不含账号、查询参数或片段。")
        if base_url.endswith(("/chat/completions", "/embeddings")):
            raise AppError("DASHSCOPE_BASE_URL 请填写兼容接口根地址，不要附加 /chat/completions。")
        mode = get("MINERU_MODE", "precision")
        if mode not in {"precision", "flash"}:
            raise AppError("MINERU_MODE 只能是 precision 或 flash。")
        chunk = number("CHUNK_BYTES", 900, 200, 1000)
        overlap = number("CHUNK_OVERLAP_BYTES", 120, 0, 500)
        if overlap >= chunk:
            raise AppError("CHUNK_OVERLAP_BYTES 必须小于 CHUNK_BYTES。")
        return cls(
            api_key=get("DASHSCOPE_API_KEY"),
            base_url=base_url,
            llm_model=get("LLM_MODEL", "qwen3.8-omni-flash"),
            embedding_model=get("EMBEDDING_MODEL", "tongyi-embedding-vision-flash"),
            embedding_endpoint=endpoint,
            embedding_dimensions=number("EMBEDDING_DIMENSIONS", 768, 1, 8192),
            tavily_key=get("TAVILY_API_KEY"),
            mineru_token=get("MINERU_TOKEN"),
            mineru_mode=mode,
            mineru_language=get("MINERU_LANGUAGE", "en"),
            mineru_ocr=get("MINERU_OCR", "true").lower() in {"true", "1", "yes"},
            data_dir=(root / get("DATA_DIR", "data")).resolve(),
            upload_dir=(root / get("UPLOAD_DIR", "uploads")).resolve(),
            max_upload_mb=number("MAX_UPLOAD_MB", 20, 1, 200),
            top_k=number("RAG_TOP_K", 5, 1, 10),
            chunk_bytes=chunk,
            overlap_bytes=overlap,
            timeout=number("REQUEST_TIMEOUT", 90, 10, 600),
            mineru_timeout=number("MINERU_TIMEOUT", 300, 30, 1800),
        )

    def chat_missing(self) -> list[str]:
        return [
            name
            for name, value in [
                ("DASHSCOPE_API_KEY", self.api_key),
                ("DASHSCOPE_BASE_URL", self.base_url),
            ]
            if not present(value)
        ]

    def require_embedding(self):
        if not present(self.api_key) or not present(self.embedding_endpoint):
            raise AppError(
                "请配置 DASHSCOPE_API_KEY 与多模态 Embedding 原生地址 DASHSCOPE_EMBEDDING_ENDPOINT。"
            )

    def ensure_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


def safe_error(exc: Exception) -> str:
    """外部 SDK 异常可能含 URL/凭证，所以不直接显示 str(exc)。"""
    if isinstance(exc, AppError):
        return str(exc)
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return "服务鉴权失败，请检查密钥、地域、业务空间以及模型权限。"
    if status == 429:
        return "服务限流或额度不足，请稍后重试并检查控制台额度。"
    if status == 400:
        return "服务拒绝了请求参数，请检查模型、接口地址和输入长度。"
    if "timeout" in type(exc).__name__.lower():
        return "服务响应超时，请稍后重试。"
    return f"操作未完成（{type(exc).__name__}）。请检查配置、网络和服务状态后重试。"
