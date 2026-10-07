"""视频字幕、分析缓存和单词本。增量建表，不修改原聊天/文档表。"""

import hashlib
import json
import re
from uuid import uuid4

from english_coach.config import AppError
from english_coach.storage import Catalog, now


def normalized(text):
    return re.sub(r"\s+", " ", text.strip().casefold())


class LearningStore:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        with catalog.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, cues TEXT NOT NULL,
                    source TEXT NOT NULL, revision TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_sessions (
                    id TEXT PRIMARY KEY, video_id TEXT NOT NULL, title TEXT NOT NULL,
                    level TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    position REAL NOT NULL DEFAULT 0, revision TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_windows (
                    cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, revision TEXT NOT NULL,
                    level TEXT NOT NULL, bucket INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS difficulties (
                    id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, video_id TEXT NOT NULL,
                    revision TEXT NOT NULL, level TEXT NOT NULL, cue_id INTEGER NOT NULL,
                    start REAL NOT NULL, end REAL NOT NULL, expression TEXT NOT NULL,
                    meaning TEXT NOT NULL, explanation TEXT NOT NULL, sentence TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    FOREIGN KEY(cache_key) REFERENCES analysis_windows(cache_key)
                );
                CREATE INDEX IF NOT EXISTS difficulty_video ON difficulties(video_id, revision, level, start);
                CREATE TABLE IF NOT EXISTS vocabulary (
                    id TEXT PRIMARY KEY, expression TEXT NOT NULL, meaning TEXT NOT NULL,
                    explanation TEXT NOT NULL, mastered INTEGER NOT NULL DEFAULT 0,
                    favorite INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS occurrences (
                    vocabulary_id TEXT NOT NULL, difficulty_id TEXT NOT NULL,
                    video_id TEXT NOT NULL, title TEXT NOT NULL, start REAL NOT NULL,
                    sentence TEXT NOT NULL, translation TEXT NOT NULL, encountered_at TEXT NOT NULL,
                    PRIMARY KEY(vocabulary_id, difficulty_id),
                    FOREIGN KEY(vocabulary_id) REFERENCES vocabulary(id) ON DELETE CASCADE
                );
            """)
            db.execute(
                "UPDATE video_sessions SET status='paused', error='服务已重启，请重新开始伴学。' WHERE status IN ('loading','analyzing','ready')"
            )

    def video(self, video_id):
        with self.catalog.connect() as db:
            row = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["cues"] = json.loads(result["cues"])
        return result

    def put_video(self, video_id, title, cues, source):
        encoded = json.dumps(cues, ensure_ascii=False, sort_keys=True)
        revision = hashlib.sha256(encoded.encode()).hexdigest()
        with self.catalog.connect() as db:
            db.execute(
                "INSERT INTO videos VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,cues=excluded.cues,source=excluded.source,revision=excluded.revision,updated_at=excluded.updated_at",
                (video_id, title, encoded, source, revision, now()),
            )
        return revision

    def create_session(self, video_id, title, level, position):
        session_id = uuid4().hex
        with self.catalog.connect() as db:
            db.execute(
                "INSERT INTO video_sessions(id,video_id,title,level,status,position,created_at) VALUES(?,?,?,?,'loading',?,?)",
                (session_id, video_id, title, level, position, now()),
            )
        return session_id

    def session(self, session_id):
        with self.catalog.connect() as db:
            row = db.execute("SELECT * FROM video_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise AppError("伴学会话不存在，请重新开始。")
        return dict(row)

    def update_session(self, session_id, **values):
        allowed = {"status", "error", "position", "revision"}
        if not values or not values.keys() <= allowed:
            raise ValueError("invalid session fields")
        with self.catalog.connect() as db:
            db.execute(
                "UPDATE video_sessions SET "
                + ",".join(f"{key}=?" for key in values)
                + " WHERE id=?",
                (*values.values(), session_id),
            )

    def finish_work(self, session_id, **values):
        # 暂停是用户指令；在途响应不能重新激活已经暂停的会话。
        with self.catalog.connect() as db:
            db.execute(
                "UPDATE video_sessions SET "
                + ",".join(f"{key}=?" for key in values)
                + " WHERE id=? AND status!='paused'",
                (*values.values(), session_id),
            )

    def cached(self, key):
        with self.catalog.connect() as db:
            return (
                db.execute("SELECT 1 FROM analysis_windows WHERE cache_key=?", (key,)).fetchone()
                is not None
            )

    def save_analysis(self, key, session, bucket, cards):
        with self.catalog.connect() as db:
            if db.execute("SELECT 1 FROM analysis_windows WHERE cache_key=?", (key,)).fetchone():
                return
            db.execute(
                "INSERT INTO analysis_windows VALUES(?,?,?,?,?,?)",
                (key, session["video_id"], session["revision"], session["level"], bucket, now()),
            )
            for card in cards:
                db.execute(
                    "INSERT INTO difficulties VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        key,
                        session["video_id"],
                        session["revision"],
                        session["level"],
                        card["cue_id"],
                        card["start"],
                        card["end"],
                        card["expression"],
                        card["meaning"],
                        card["explanation"],
                        card["sentence"],
                        card["translation"],
                    ),
                )

    def cards(self, session_id, position):
        session = self.session(session_id)
        with self.catalog.connect() as db:
            rows = db.execute(
                "SELECT * FROM difficulties WHERE video_id=? AND revision=? AND level=? AND end>=? AND start<=? ORDER BY start",
                (
                    session["video_id"],
                    session["revision"],
                    session["level"],
                    max(0, position - 30),
                    position + 125,
                ),
            ).fetchall()
            mastered = {
                normalized(row[0])
                for row in db.execute("SELECT expression FROM vocabulary WHERE mastered=1")
            }
        return [dict(row) for row in rows if normalized(row["expression"]) not in mastered]

    def expose(self, session_id, card_id, position):
        session = self.session(session_id)
        if session["status"] == "paused":
            raise AppError("伴学已暂停。")
        with self.catalog.connect() as db:
            row = db.execute(
                "SELECT * FROM difficulties WHERE id=? AND video_id=? AND revision=? AND level=?",
                (card_id, session["video_id"], session["revision"], session["level"]),
            ).fetchone()
            if not row or not row["start"] - 1 <= position <= row["end"] + 2:
                raise AppError("难点不属于当前播放位置，未加入单词本。")
            card = dict(row)
            key = hashlib.sha256(
                (normalized(card["expression"]) + "\0" + normalized(card["meaning"])).encode()
            ).hexdigest()
            db.execute(
                "INSERT OR IGNORE INTO vocabulary(id,expression,meaning,explanation,created_at) VALUES(?,?,?,?,?)",
                (key, card["expression"], card["meaning"], card["explanation"], now()),
            )
            db.execute(
                "INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    card_id,
                    session["video_id"],
                    session["title"],
                    card["start"],
                    card["sentence"],
                    card["translation"],
                    now(),
                ),
            )
        return key

    def vocabulary(self, query="", video_id="", mastered=None, favorite=False):
        where, params = [], []
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(v.expression LIKE ? ESCAPE '\\' OR v.meaning LIKE ? ESCAPE '\\')")
            params.extend([f"%{escaped}%"] * 2)
        if video_id:
            where.append(
                "EXISTS(SELECT 1 FROM occurrences o WHERE o.vocabulary_id=v.id AND o.video_id=?)"
            )
            params.append(video_id)
        if mastered is not None:
            where.append("v.mastered=?")
            params.append(int(mastered))
        if favorite:
            where.append("v.favorite=1")
        with self.catalog.connect() as db:
            rows = db.execute(
                "SELECT v.* FROM vocabulary v"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY created_at DESC LIMIT 200",
                params,
            ).fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                entry["occurrences"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM occurrences WHERE vocabulary_id=? ORDER BY encountered_at DESC",
                        (row["id"],),
                    )
                ]
                result.append(entry)
        return result

    def patch_vocabulary(self, entry_id, **values):
        if not values or not values.keys() <= {"mastered", "favorite"}:
            raise AppError("请选择需要修改的学习状态。")
        with self.catalog.connect() as db:
            cursor = db.execute(
                "UPDATE vocabulary SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                (*[int(v) for v in values.values()], entry_id),
            )
            if not cursor.rowcount:
                raise AppError("单词本条目不存在。")

    def encountered_videos(self):
        with self.catalog.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT video_id, MAX(title) AS title, COUNT(DISTINCT vocabulary_id) AS count FROM occurrences GROUP BY video_id ORDER BY MAX(encountered_at) DESC"
                )
            ]

    def context(self, video_id, position):
        video = self.video(video_id)
        if not video:
            return {"error": "尚未导入该视频字幕。"}
        return {
            "video_id": video_id,
            "title": video["title"],
            "source": video["source"],
            "cues": [
                c
                for c in video["cues"]
                if c["end"] >= position - 20 and c["start"] <= position + 20
            ][:20],
        }
