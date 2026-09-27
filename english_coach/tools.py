"""Agent 工具：始终保留来源，并将服务错误转为模型可理解的结果。"""

import json
from datetime import date
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from langchain_tavily._utilities import TavilySearchAPIWrapper

from english_coach.config import AppError, Settings, present, safe_error


class TimedTavilyAPI(TavilySearchAPIWrapper):
    """锁定版本的 Tavily 同步封装未设网络超时，在边界加超时以免页面永久等待。"""

    request_timeout: int = 90

    def raw_results(self, **kwargs):
        params = {key: value for key, value in kwargs.items() if value is not None}
        with httpx.Client(timeout=self.request_timeout, follow_redirects=False) as client:
            response = client.post(
                "https://api.tavily.com/search",
                json=params,
                headers={"Authorization": f"Bearer {self.tavily_api_key.get_secret_value()}"},
            )
            response.raise_for_status()
            return response.json()


def build_tools(settings: Settings, knowledge, sources: list[dict], on_status):
    @tool
    def search_materials(query: str, filename: str = "") -> str:
        """检索用户上传的英语笔记/教材。query 应为结合上下文的简短查询；filename 可指定完整文件名。"""
        on_status("正在查阅上传资料…")
        try:
            docs = knowledge.search(query, filename)
        except Exception as exc:
            return "资料检索失败：" + safe_error(exc)
        if not docs:
            return "没有找到资料片段。可能尚未上传文件、文件名不存在或资料中没有相关内容。不得声称已从资料得到答案。"
        snippets = []
        for doc in docs:
            existing = next(
                (s for s in sources if s.get("chunk_id") == doc.metadata["chunk_id"]), None
            )
            if not existing:
                number = 1 + sum(s["kind"] == "document" for s in sources)
                existing = {
                    "kind": "document",
                    "label": f"资料{number}",
                    "filename": doc.metadata["filename"],
                    "page": doc.metadata.get("page"),
                    "section": " / ".join(
                        str(doc.metadata[k]) for k in ("h1", "h2", "h3") if k in doc.metadata
                    ),
                    "chunk_id": doc.metadata["chunk_id"],
                    "excerpt": doc.page_content,
                }
                sources.append(existing)
            snippets.append(existing)
        return json.dumps(
            {"note": "以下仅为候选片段，请判断相关性。", "results": snippets}, ensure_ascii=False
        )

    @tool
    def web_search(query: str) -> str:
        """搜索近期英语用法、slang、流行程度及真实语境。时效问题应包含当前年份，比较多个来源。"""
        on_status("正在联网核查英语用法…")
        if not present(settings.tavily_key):
            return "联网搜索未配置 TAVILY_API_KEY，无法核实最新用法。请明确说明此限制。"
        try:
            search = TavilySearch(
                api_wrapper=TimedTavilyAPI(
                    tavily_api_key=settings.tavily_key, request_timeout=settings.timeout
                ),
                max_results=5,
                topic="general",
                include_raw_content=False,
                include_answer=False,
            )
            result = search.invoke({"query": query})
            if isinstance(result, str):
                result = json.loads(result)
            if result.get("error"):
                raise AppError("Tavily 请求未成功，请检查密钥、额度或网络连接。")
            rows = result.get("results", [])
            if not rows:
                return "本次搜索未返回可用来源，不能据此判断表达是否流行。"
            snippets = []
            for row in rows:
                url = row.get("url", "")
                if urlsplit(url).scheme not in {"http", "https"}:
                    continue
                existing = next((s for s in sources if s.get("url") == url), None)
                if not existing:
                    number = 1 + sum(s["kind"] == "web" for s in sources)
                    existing = {
                        "kind": "web",
                        "label": f"网络{number}",
                        "url": url,
                        "title": row.get("title", "来源网页"),
                        "excerpt": str(row.get("content", ""))[:3000],
                        "retrieved_at": date.today().isoformat(),
                        "published_date": row.get("published_date", ""),
                    }
                    sources.append(existing)
                snippets.append(existing)
            return json.dumps(
                {"note": "检索日期不等于发布日期。搜索结果不是流行度统计。", "results": snippets},
                ensure_ascii=False,
            )
        except Exception as exc:
            return "联网搜索失败：" + safe_error(exc) + " 不得假装完成了实时核查。"

    return [search_materials, web_search]
