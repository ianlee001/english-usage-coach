"""保存可展示的聊天记录与资料目录。Agent 内部状态由独立 checkpointer 保存。"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(UTC).isoformat()


class Catalog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
                    sources TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES conversations(id)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
                    chunks INTEGER NOT NULL, created_at TEXT NOT NULL
                );
            """)
            # 进程崩溃后保留用户输入，明确标注未完成，避免伪造成功回答。
            conn.execute(
                "UPDATE turns SET status='failed', error='上次请求被中断，请重新发送或新建聊天。' WHERE status='pending'"
            )

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def new_conversation(self) -> str:
        thread_id = uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO conversations VALUES (?, ?, ?)", (thread_id, "新聊天", now()))
        return thread_id

    def conversations(self) -> list[dict]:
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC")
            ]

    def turns(self, thread_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM turns WHERE thread_id=? ORDER BY created_at, rowid", (thread_id,)
                )
            ]
        for row in rows:
            row["sources"] = json.loads(row["sources"])
        return rows

    def begin_turn(self, thread_id: str, question: str) -> str:
        turn_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO turns(id,thread_id,question,status,created_at) VALUES(?,?,?,'pending',?)",
                (turn_id, thread_id, question, now()),
            )
            conn.execute(
                "UPDATE conversations SET title=CASE WHEN title='新聊天' THEN ? ELSE title END, updated_at=? WHERE id=?",
                (question[:32], now(), thread_id),
            )
        return turn_id

    def finish_turn(self, turn_id: str, answer: str, sources: list[dict], error: str = ""):
        with self.connect() as conn:
            conn.execute(
                "UPDATE turns SET answer=?, sources=?, status=?, error=? WHERE id=?",
                (
                    answer,
                    json.dumps(sources, ensure_ascii=False),
                    "failed" if error else "complete",
                    error,
                    turn_id,
                ),
            )

    def has_document(self, doc_id: str) -> bool:
        with self.connect() as conn:
            return (
                conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is not None
            )

    def add_document(self, doc_id: str, name: str, path: Path, chunks: int):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?)", (doc_id, name, str(path), chunks, now())
            )

    def documents(self) -> list[dict]:
        with self.connect() as conn:
            return [
                dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY created_at DESC")
            ]
