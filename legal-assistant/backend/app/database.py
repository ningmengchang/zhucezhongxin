from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    source_level TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    authority TEXT,
                    official_url TEXT,
                    publication_date TEXT,
                    effective_date TEXT,
                    expiry_date TEXT,
                    version_of TEXT,
                    review_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS passages (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    article TEXT,
                    paragraph TEXT,
                    page INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    position INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_passages_document ON passages(document_id);
                CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(review_status, source_level);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    conversation_id TEXT,
                    as_of_date TEXT,
                    status TEXT NOT NULL,
                    answer_json TEXT,
                    error TEXT,
                    events_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_conversation
                ON jobs(conversation_id, created_at);
                """
            )

    def get_document_by_hash(self, digest: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE sha256 = ?", (digest,)).fetchone()
            return dict(row) if row else None

    def delete_document_by_hash(self, digest: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE sha256 = ?", (digest,))

    def insert_document(self, document: dict[str, Any], passages: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            columns = ",".join(document.keys())
            marks = ",".join("?" for _ in document)
            db.execute(f"INSERT INTO documents ({columns}) VALUES ({marks})", tuple(document.values()))
            db.executemany(
                """
                INSERT INTO passages
                    (id, document_id, article, paragraph, page, text, normalized_text, position)
                VALUES
                    (:id, :document_id, :article, :paragraph, :page, :text, :normalized_text, :position)
                """,
                passages,
            )

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT d.*, COUNT(p.id) AS passage_count
                FROM documents d LEFT JOIN passages p ON p.document_id = d.id
                GROUP BY d.id ORDER BY d.created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
            return dict(row) if row else None

    def update_document_review(
        self, document_id: str, review_status: str, source_level: str | None, note: str | None
    ) -> bool:
        with self.connect() as db:
            if source_level:
                result = db.execute(
                    """UPDATE documents SET review_status=?, source_level=?, review_note=?, updated_at=?
                    WHERE id=?""",
                    (review_status, source_level, note, utc_now(), document_id),
                )
            else:
                result = db.execute(
                    """UPDATE documents SET review_status=?, review_note=?, updated_at=? WHERE id=?""",
                    (review_status, note, utc_now(), document_id),
                )
            return result.rowcount > 0

    def version_chain(self, document_id: str) -> list[dict[str, Any]]:
        document = self.get_document(document_id)
        if not document:
            return []
        root = document.get("version_of") or document_id
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE id=? OR version_of=? ORDER BY publication_date, created_at",
                (root, root),
            ).fetchall()
            return [dict(row) for row in rows]

    def published_passages(
        self, as_of_date: str | None = None, *, include_future_rules: bool = False
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        date_filter = ""
        if as_of_date:
            date_filter = """
                AND (d.publication_date IS NULL OR d.publication_date <= ?)
                AND (d.expiry_date IS NULL OR d.expiry_date >= ?)
            """
            params.extend([as_of_date, as_of_date])
            if not include_future_rules:
                date_filter += " AND (d.effective_date IS NULL OR d.effective_date <= ?)"
                params.append(as_of_date)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT p.*, d.title AS document_title, d.filename, d.source_level,
                       d.official_url, d.publication_date, d.effective_date, d.expiry_date
                FROM passages p JOIN documents d ON d.id = p.document_id
                WHERE d.review_status='published' {date_filter}
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_passage(self, evidence_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT p.*, d.title AS document_title, d.filename, d.source_level, d.official_url,
                       d.publication_date, d.effective_date, d.expiry_date
                FROM passages p JOIN documents d ON d.id=p.document_id WHERE p.id=?
                """,
                (evidence_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_job(
        self, job_id: str, question: str, conversation_id: str | None, as_of_date: str | None
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO jobs
                (id,question,conversation_id,as_of_date,status,events_json,created_at,updated_at)
                VALUES (?,?,?,?,?,'[]',?,?)""",
                (job_id, question, conversation_id, as_of_date, "queued", now, now),
            )

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        answer: dict[str, Any] | None = None,
        error: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as db:
            row = db.execute("SELECT events_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return
            events = json.loads(row["events_json"] or "[]")
            if event:
                events.append(event)
            fields = ["events_json=?", "updated_at=?"]
            values: list[Any] = [json.dumps(events, ensure_ascii=False), utc_now()]
            if status is not None:
                fields.append("status=?")
                values.append(status)
            if answer is not None:
                fields.append("answer_json=?")
                values.append(json.dumps(answer, ensure_ascii=False))
            if error is not None:
                fields.append("error=?")
                values.append(error)
            values.append(job_id)
            db.execute(f"UPDATE jobs SET {','.join(fields)} WHERE id=?", values)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["events"] = json.loads(result.pop("events_json") or "[]")
            result["answer"] = json.loads(result.pop("answer_json")) if result.get("answer_json") else None
            result.pop("answer_json", None)
            return result

    def conversation_history(
        self, conversation_id: str | None, *, exclude_job_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        if not conversation_id or limit <= 0:
            return []
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, question, answer_json, created_at
                FROM jobs
                WHERE conversation_id=? AND id<>? AND status='completed' AND answer_json IS NOT NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, exclude_job_id, limit),
            ).fetchall()
        history = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["answer"] = json.loads(item.pop("answer_json"))
            except (json.JSONDecodeError, TypeError):
                continue
            history.append(item)
        return history
