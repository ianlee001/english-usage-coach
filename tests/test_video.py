import json
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from english_coach.config import AppError
from english_coach.learning import LearningStore
from english_coach.storage import Catalog
from english_coach.video import (
    VideoService,
    analyze_difficulties,
    fetch_transcript,
    normalize_cues,
    parse_subtitles,
)

VIDEO = "abcdefghijk"
SRT = "1\n00:00:01,000 --> 00:00:06,000\nI chickened out.\n\n2\n00:01:05,000 --> 00:01:09,000\nTake it easy.\n"


@pytest.mark.parametrize("manual_available", [True, False])
def test_youtube_prefers_manual_english_and_falls_back_to_generated(monkeypatch, manual_available):
    calls = []

    def track(generated):
        def fetch():
            calls.append(generated)
            return SimpleNamespace(
                to_raw_data=lambda: [{"start": 1, "duration": 4, "text": "Take it easy."}]
            )

        return SimpleNamespace(language_code="en", is_generated=generated, fetch=fetch)

    tracks = [track(True)]
    if manual_available:
        tracks.append(track(False))
    monkeypatch.setattr(
        "english_coach.video.YouTubeTranscriptApi",
        lambda **_: SimpleNamespace(list=lambda _: tracks),
    )
    cues, source = fetch_transcript(VIDEO)
    assert calls == [not manual_available]
    assert cues[0]["text"] == "Take it easy."
    assert ("人工" if manual_available else "自动") in source


@pytest.fixture
def learning(settings):
    return LearningStore(Catalog(settings.data_dir / "catalog.sqlite"))


def setup_cards(store, video_id=VIDEO):
    cues = parse_subtitles(SRT)
    revision = store.put_video(video_id, "测试视频", cues, "test")
    session_id = store.create_session(video_id, "测试视频", "中级", 0)
    store.update_session(session_id, status="ready", revision=revision)
    session = store.session(session_id)
    card = {
        "cue_id": 0,
        "start": 1,
        "end": 6,
        "expression": "chickened out",
        "meaning": "临阵退缩",
        "explanation": "口语短语",
        "sentence": cues[0]["text"],
        "translation": "我退缩了。",
    }
    store.save_analysis("key-" + session_id, session, 0, [card])
    return session_id, store.cards(session_id, 0)[0]


def test_subtitles_merge_rolling_text_and_keep_timestamps():
    cues = normalize_cues(
        [
            {"start": 1, "duration": 2, "text": "I was going"},
            {"start": 2, "duration": 3, "text": "was going to join."},
        ]
    )
    assert cues == [{"id": 0, "start": 1.0, "end": 5.0, "text": "I was going to join."}]
    vtt = parse_subtitles(
        "WEBVTT\n\ncue-a\n00:01.000 --> 00:05.000 align:start\n<i>Keep it down.</i>\n"
    )
    assert vtt[0]["text"] == "Keep it down."
    assert vtt[0]["start"] == 1
    with pytest.raises(AppError):
        parse_subtitles("no timestamps")
    with pytest.raises(AppError):
        normalize_cues([{"start": float("nan"), "duration": 2, "text": "x"}])


def test_cached_cards_not_saved_until_exposure_and_exposure_is_idempotent(learning):
    session_id, card = setup_cards(learning)
    assert learning.vocabulary() == []
    with pytest.raises(AppError):
        learning.expose(session_id, card["id"], 100)
    entry_id = learning.expose(session_id, card["id"], 2)
    assert learning.expose(session_id, card["id"], 2) == entry_id
    entries = learning.vocabulary()
    assert len(entries) == len(entries[0]["occurrences"]) == 1
    learning.patch_vocabulary(entry_id, mastered=True, favorite=True)
    assert learning.cards(session_id, 0) == []
    assert len(learning.vocabulary("退缩", VIDEO, True, True)) == 1
    assert learning.vocabulary("%") == []
    assert learning.vocabulary(video_id="other") == []


def test_wrong_video_and_paused_session_cannot_save(learning):
    session_id, card = setup_cards(learning)
    other, _ = setup_cards(learning, "12345678901")
    with pytest.raises(AppError):
        learning.expose(other, card["id"], 2)
    learning.update_session(session_id, status="paused")
    with pytest.raises(AppError):
        learning.expose(session_id, card["id"], 2)


def test_model_cannot_invent_timestamps_or_unknown_phrases(settings, monkeypatch):
    class Model:
        def invoke(self, messages):
            return AIMessage(
                content=json.dumps(
                    {
                        "difficulties": [
                            {
                                "cue_id": 0,
                                "expression": "chickened out",
                                "meaning": "退缩",
                                "explanation": "说明",
                                "translation": "翻译",
                                "start": 999,
                            },
                            {
                                "cue_id": 900,
                                "expression": "ghost",
                                "meaning": "x",
                                "explanation": "x",
                                "translation": "x",
                            },
                            {
                                "cue_id": 0,
                                "expression": "invented phrase",
                                "meaning": "x",
                                "explanation": "x",
                                "translation": "x",
                            },
                        ]
                    }
                )
            )

    monkeypatch.setattr("english_coach.video.init_chat_model", lambda **_: Model())
    result = analyze_difficulties(settings, parse_subtitles(SRT), "中级", 0)
    assert len(result) == 1
    assert result[0]["start"] == 1
    assert result[0]["sentence"] == "I chickened out."


def test_pause_during_analysis_keeps_paused_and_does_not_save_vocabulary(settings, learning):
    session_id, _ = setup_cards(learning)
    started, release = threading.Event(), threading.Event()
    calls = []

    def analyzer(*args):
        calls.append(args[-1])
        started.set()
        assert release.wait(5)
        return []

    service = VideoService(settings, learning, analyzer=analyzer)
    service.window(session_id, 2)
    assert started.wait(5)
    learning.update_session(session_id, status="paused")
    release.set()
    service.close()
    assert learning.session(session_id)["status"] == "paused"
    assert learning.vocabulary() == []
    assert calls == [0]


def test_cache_prevents_repeat_model_calls(settings, learning):
    session_id, _ = setup_cards(learning)
    calls = []
    service = VideoService(settings, learning, analyzer=lambda *args: calls.append(args[-1]) or [])
    service.analyze_window(session_id, 0)
    service.analyze_window(session_id, 0)
    assert calls == [0, 1]
    service.close()


def test_restart_preserves_words_and_context_but_pauses_sessions(learning):
    session_id, card = setup_cards(learning)
    learning.expose(session_id, card["id"], 3)
    restart = LearningStore(learning.catalog)
    assert len(restart.vocabulary()) == 1
    assert restart.session(session_id)["status"] == "paused"
    assert restart.context(VIDEO, 3)["cues"][0]["text"] == "I chickened out."
