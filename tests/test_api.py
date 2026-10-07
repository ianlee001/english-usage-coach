import json
import sqlite3
import time

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from backend.server import create_app
from english_coach.config import AppError
from english_coach.runtime import Runtime
from english_coach.storage import Catalog
from english_coach.tools import build_tools
from english_coach.video import VideoService
from tests.test_agent_ui import ScriptedModel
from tests.test_video import SRT, VIDEO


def test_legacy_chat_is_backed_up_and_preserved(settings):
    catalog = Catalog(settings.data_dir / "catalog.sqlite")
    thread = catalog.new_conversation()
    turn = catalog.begin_turn(thread, "旧问题")
    catalog.finish_turn(turn, "旧答案", [])
    with TestClient(create_app(settings)) as client:
        client.get("/")
        history = client.get(f"/api/conversations/{thread}/messages").json()
        assert history[0]["answer"] == "旧答案"
    backup = settings.data_dir / "backups" / "before_web_migration" / "catalog.sqlite"
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT answer FROM turns WHERE id=?", (turn,)).fetchone()[0] == "旧答案"


def test_auth_csrf_and_settings_never_expose_model_key(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/health").status_code == 200
        page = client.get("/")
        assert "我的单词本" in page.text
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        config = client.get("/api/settings")
        assert config.status_code == 200 and "test-only-key" not in config.text
        assert client.post("/api/conversations").status_code == 403
        assert (
            client.post(
                "/api/conversations", headers={"origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/conversations", headers={"origin": "http://testserver"}).status_code
            == 201
        )
        token = client.get("/api/pairing-token").json()["token"]
        client.cookies.clear()
        assert (
            client.post(
                "/api/conversations", headers={"authorization": f"Bearer {token}"}
            ).status_code
            == 201
        )
        assert client.get("/api/settings", headers={"host": "evil.example"}).status_code == 400


def test_rest_chat_stream_history_and_error_recovery(settings, monkeypatch):
    monkeypatch.setattr(
        "english_coach.runtime.init_chat_model",
        lambda **_: ScriptedModel(responses=[AIMessage(content="小声一点。")]),
    )
    with TestClient(create_app(settings)) as client:
        client.get("/")
        client.headers["origin"] = "http://testserver"
        thread = client.post("/api/conversations").json()["id"]
        response = client.post(
            f"/api/conversations/{thread}/messages", json={"question": "keep it down?"}
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "done"
        assert events[-1]["data"]["answer"] == "小声一点。"
        assert client.get(f"/api/conversations/{thread}/messages").json()[0]["status"] == "complete"
        assert (
            client.post(
                "/api/conversations/missing/messages", json={"question": "hello"}
            ).status_code
            == 404
        )
        monkeypatch.setattr("english_coach.runtime.init_chat_model", lambda **_: ScriptedModel())
        failed = client.post(f"/api/conversations/{thread}/messages", json={"question": "again"})
        assert '"type": "error"' in failed.text
        assert "test-only-key" not in failed.text


def test_subtitle_import_video_analysis_exposure_and_vocab_tools(settings):
    runtime = Runtime(settings)

    def analyzer(config, cues, level, bucket):
        if bucket:
            return []
        return [
            {
                "cue_id": 0,
                "start": 1,
                "end": 6,
                "expression": "chickened out",
                "meaning": "临阵退缩",
                "explanation": "口语",
                "sentence": cues[0]["text"],
                "translation": "我退缩了。",
            }
        ]

    def fetcher(_):
        raise AssertionError("imported subtitles must not use the network")

    service = VideoService(settings, runtime.learning, fetcher=fetcher, analyzer=analyzer)
    with TestClient(create_app(settings, runtime, service)) as client:
        client.get("/")
        client.headers["origin"] = "http://testserver"
        response = client.post(
            f"/api/videos/{VIDEO}/subtitles", json={"title": "测试", "content": SRT}
        )
        assert response.status_code == 200
        sid = client.post("/api/video-sessions", json={"video_id": VIDEO, "title": "测试"}).json()[
            "id"
        ]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(f"/api/video-sessions/{sid}").json()
            if state["status"] == "ready":
                break
            time.sleep(0.01)
        assert state["status"] == "ready"
        client.post(f"/api/video-sessions/{sid}/window", json={"position": 2})
        while time.monotonic() < deadline:
            state = client.get(f"/api/video-sessions/{sid}").json()
            if state["cards"]:
                break
            time.sleep(0.01)
        assert client.get("/api/vocabulary").json() == []
        card = state["cards"][0]
        saved = client.post(
            f"/api/video-sessions/{sid}/exposures",
            json={"difficulty_id": card["id"], "position": 3},
        )
        assert saved.status_code == 200
        entry_id = saved.json()["vocabulary_id"]
        assert (
            client.patch(f"/api/vocabulary/{entry_id}", json={"favorite": True}).status_code == 200
        )
        assert len(client.get("/api/vocabulary?favorite=true").json()) == 1
        tools = {
            t.name: t
            for t in build_tools(settings, runtime.knowledge, [], lambda _: None, runtime.learning)
        }
        assert "chickened out" in tools["search_vocabulary"].invoke({"query": "退缩"})
        assert "chickened out" in tools["get_video_context"].invoke(
            {"video_id": VIDEO, "position": 2}
        )
    runtime.close()


def test_import_validation_and_fetch_failure_are_actionable(settings):
    runtime = Runtime(settings)
    service = VideoService(
        settings,
        runtime.learning,
        fetcher=lambda _: (_ for _ in ()).throw(AppError("字幕不可用，可导入 SRT/VTT。")),
    )
    with TestClient(create_app(settings, runtime, service)) as client:
        client.get("/")
        client.headers["origin"] = "http://testserver"
        assert (
            client.post(
                "/api/video-sessions", json={"video_id": "https://evil.example"}
            ).status_code
            == 422
        )
        assert (
            client.post(f"/api/videos/{VIDEO}/subtitles", json={"content": "bad"}).status_code
            == 400
        )
        sid = client.post("/api/video-sessions", json={"video_id": VIDEO}).json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(f"/api/video-sessions/{sid}").json()
            if state["status"] == "failed":
                break
            time.sleep(0.01)
        assert state["status"] == "failed"
        assert "SRT/VTT" in state["error"]
    runtime.close()
