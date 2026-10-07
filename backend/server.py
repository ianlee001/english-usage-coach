"""REST、NDJSON 聊天流、静态前端和本地扩展认证。"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from english_coach.config import ROOT, AppError, Settings, safe_error
from english_coach.runtime import Runtime
from english_coach.video import Level, VideoService, parse_subtitles, validate_video_id

logger = logging.getLogger("uvicorn.error.coach")


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=6000)


class SessionInput(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str = Field(default="", max_length=300)
    level: Level = "中级"
    position: float = Field(default=0, ge=0, le=14400, allow_inf_nan=False)


class PositionInput(BaseModel):
    position: float = Field(ge=0, le=14400, allow_inf_nan=False)


class ExposureInput(PositionInput):
    difficulty_id: str = Field(min_length=1, max_length=64)


class VocabularyPatch(BaseModel):
    mastered: bool | None = None
    favorite: bool | None = None


class SubtitleInput(BaseModel):
    title: str = Field(default="", max_length=300)
    content: str = Field(min_length=1, max_length=2_000_000)


def backup_legacy(data_dir):
    marker = data_dir / "migration_v2.done"
    if marker.exists():
        return
    backup = data_dir / "backups" / "before_web_migration"
    for name in ("catalog.sqlite", "checkpoints.sqlite"):
        source, destination = data_dir / name, backup / name
        if source.exists() and not destination.exists():
            backup.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(source) as old, sqlite3.connect(destination) as target:
                old.backup(target)
    marker.write_text("Additive schema migration; original data retained.\n", encoding="utf-8")


def local_token(data_dir):
    path = data_dir / "local_api_token.txt"
    if not path.exists():
        with path.open("x", encoding="utf-8") as file:
            file.write(secrets.token_urlsafe(32))
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise AppError("本地连接凭证无效，请移除 data/local_api_token.txt 后重启以重新生成。")
    return token


def create_app(settings=None, runtime=None, video_service=None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.load()
        config.ensure_dirs()
        backup_legacy(config.data_dir)
        app.state.settings = config
        app.state.token = local_token(config.data_dir)
        app.state.runtime = runtime or await asyncio.to_thread(Runtime, config)
        app.state.video = video_service or VideoService(config, app.state.runtime.learning)
        app.state.tasks = set()
        app.state.chat_busy = False
        app.state.ingest_busy = False
        yield
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        await asyncio.to_thread(app.state.video.close)
        if runtime is None:
            await asyncio.to_thread(app.state.runtime.close)

    app = FastAPI(
        title="English Usage Coach",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def request_log(request, call_next):
        request_id, started = uuid4().hex[:12], time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        logger.info(
            "%s %s %s %.3fs id=%s",
            request.method,
            request.url.path,
            response.status_code,
            time.monotonic() - started,
            request_id,
        )
        return response

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse({"detail": safe_error(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"detail": "请求参数无效，请检查输入内容、时间或文件大小。"}, status_code=422
        )

    @app.exception_handler(Exception)
    async def unknown_error(request, exc):
        logger.error("Request failed: %s", type(exc).__name__)
        return JSONResponse({"detail": safe_error(exc)}, status_code=500)

    def authenticate(request: Request):
        bearer, token = request.headers.get("authorization", ""), request.app.state.token
        if bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], token):
            return
        if not hmac.compare_digest(request.cookies.get("coach_session", ""), token):
            raise HTTPException(401, "请打开主页面，或在扩展中填写连接码。")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin", "") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "请求来源不匹配，请从主页面操作。")

    auth = [Depends(authenticate)]

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        response = FileResponse(ROOT / "frontend" / "index.html")
        response.set_cookie(
            "coach_session", request.app.state.token, httponly=True, samesite="strict"
        )
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/settings", dependencies=auth)
    def public_settings():
        config = app.state.settings
        return {
            "model": config.llm_model,
            "chat_missing": config.chat_missing(),
            "max_upload_mb": config.max_upload_mb,
        }

    @app.get("/api/pairing-token", dependencies=auth)
    def pairing():
        return {"token": app.state.token}

    @app.get("/api/conversations", dependencies=auth)
    def conversations():
        return app.state.runtime.catalog.conversations()

    @app.post("/api/conversations", dependencies=auth, status_code=201)
    def new_conversation():
        return {"id": app.state.runtime.catalog.new_conversation()}

    def require_conversation(thread_id):
        if not any(row["id"] == thread_id for row in app.state.runtime.catalog.conversations()):
            raise HTTPException(404, "聊天不存在。")

    @app.get("/api/conversations/{thread_id}/messages", dependencies=auth)
    def messages(thread_id: str):
        require_conversation(thread_id)
        return app.state.runtime.catalog.turns(thread_id)

    @app.post("/api/conversations/{thread_id}/messages", dependencies=auth)
    async def chat(thread_id: str, body: ChatInput):
        require_conversation(thread_id)
        if app.state.chat_busy or app.state.ingest_busy:
            raise HTTPException(409, "正在回答或导入资料，请完成后再发送。")
        app.state.chat_busy = True
        loop, queue = asyncio.get_running_loop(), asyncio.Queue()
        disconnected = threading.Event()

        def emit(kind, value):
            if not disconnected.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, {"type": kind, "data": value})

        def worker():
            try:
                answer, sources = app.state.runtime.chat(
                    thread_id,
                    body.question,
                    on_text=lambda value: emit("text", value),
                    on_status=lambda value: emit("status", value),
                )
                emit("done", {"answer": answer, "sources": sources})
            except Exception as exc:
                emit("error", safe_error(exc))
            finally:
                emit("end", None)

        async def run():
            try:
                await asyncio.to_thread(worker)
            finally:
                app.state.chat_busy = False

        task = asyncio.create_task(run())
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)

        async def stream():
            try:
                while True:
                    event = await queue.get()
                    if event["type"] == "end":
                        break
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            finally:
                # 浏览器断开后仍完成 SQLite 保存，但不继续堆积发送缓冲。
                disconnected.set()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/api/documents", dependencies=auth)
    def documents():
        return [
            {k: v for k, v in row.items() if k != "path"}
            for row in app.state.runtime.catalog.documents()
        ]

    @app.post("/api/documents", dependencies=auth)
    async def upload(file: UploadFile = File(...)):
        if app.state.chat_busy or app.state.ingest_busy:
            raise HTTPException(409, "请等待当前回答或导入完成。")
        app.state.ingest_busy = True
        try:
            data = await file.read(app.state.settings.max_upload_mb * 1024 * 1024 + 1)
            if len(data) > app.state.settings.max_upload_mb * 1024 * 1024:
                raise HTTPException(413, "文件超过上传限制。")
            result = await asyncio.to_thread(
                app.state.runtime.ingest, file.filename or "document.txt", data
            )
            return {"result": result}
        finally:
            app.state.ingest_busy = False
            await file.close()

    @app.get("/api/vocabulary", dependencies=auth)
    def vocabulary(
        query: str = "", video_id: str = "", mastered: bool | None = None, favorite: bool = False
    ):
        return app.state.runtime.learning.vocabulary(query[:200], video_id, mastered, favorite)

    @app.get("/api/videos", dependencies=auth)
    def encountered_videos():
        return app.state.runtime.learning.encountered_videos()

    @app.patch("/api/vocabulary/{entry_id}", dependencies=auth)
    def patch_vocabulary(entry_id: str, body: VocabularyPatch):
        app.state.runtime.learning.patch_vocabulary(entry_id, **body.model_dump(exclude_none=True))
        return {"ok": True}

    @app.post("/api/videos/{video_id}/subtitles", dependencies=auth)
    def subtitles(video_id: str, body: SubtitleInput):
        validate_video_id(video_id)
        cues = parse_subtitles(body.content)
        revision = app.state.runtime.learning.put_video(
            video_id, body.title or video_id, cues, "手动导入英文字幕"
        )
        return {"cues": len(cues), "revision": revision}

    @app.post("/api/video-sessions", dependencies=auth, status_code=202)
    def video_session(body: SessionInput):
        session_id = app.state.video.create(body.video_id, body.title, body.level, body.position)
        return {"id": session_id}

    @app.get("/api/video-sessions/{session_id}", dependencies=auth)
    def session_status(session_id: str, position: float = 0):
        if not 0 <= position <= 14400:
            raise HTTPException(422, "播放时间无效。")
        store = app.state.runtime.learning
        session = store.session(session_id)
        video = store.video(session["video_id"])
        return {
            **session,
            "source": video["source"] if video else "",
            "cards": app.state.video.cards(session_id, position),
        }

    @app.post("/api/video-sessions/{session_id}/window", dependencies=auth, status_code=202)
    def analyze_window(session_id: str, body: PositionInput):
        app.state.video.window(session_id, body.position)
        return {"ok": True}

    @app.post("/api/video-sessions/{session_id}/pause", dependencies=auth)
    def pause(session_id: str):
        app.state.runtime.learning.session(session_id)
        app.state.runtime.learning.update_session(session_id, status="paused")
        return {"ok": True}

    @app.post("/api/video-sessions/{session_id}/exposures", dependencies=auth)
    def exposure(session_id: str, body: ExposureInput):
        return {
            "vocabulary_id": app.state.runtime.learning.expose(
                session_id, body.difficulty_id, body.position
            )
        }

    app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
    return app


def main():
    import uvicorn

    # 单进程本地模式：后台任务和 SQLite 锁由同一个进程管理。
    uvicorn.run(
        "backend.server:create_app",
        factory=True,
        host="127.0.0.1",
        port=int(os.getenv("COACH_PORT", "8000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
