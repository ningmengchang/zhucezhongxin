from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .search_index import token_document


SEARCH_INDEX_VERSION = "1"


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
                    jurisdiction TEXT NOT NULL,
                    authority TEXT,
                    official_url TEXT,
                    publication_date TEXT,
                    effective_date TEXT,
                    expiry_date TEXT,
                    version_of TEXT,
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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoke_reason TEXT,
                    created_at TEXT NOT NULL,
                    login_ip TEXT,
                    user_agent TEXT,
                    browser TEXT,
                    operating_system TEXT,
                    device TEXT,
                    last_seen_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                ON auth_sessions(user_id, created_at);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    created_by TEXT,
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

                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(
                    evidence_id UNINDEXED,
                    body,
                    title,
                    article,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            job_columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "created_by" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN created_by TEXT")
            session_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(auth_sessions)").fetchall()
            }
            for column in (
                "login_ip", "user_agent", "browser", "operating_system", "device",
                "last_seen_at",
            ):
                if column not in session_columns:
                    db.execute(f"ALTER TABLE auth_sessions ADD COLUMN {column} TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_sessions_last_seen "
                "ON auth_sessions(last_seen_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created_by_created "
                "ON jobs(created_by, created_at)"
            )
            self._remove_level_and_review_metadata(db)
            self._ensure_search_index(db)

    @staticmethod
    def _ensure_search_index(db: sqlite3.Connection) -> None:
        passage_count = db.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        fts_count = db.execute("SELECT COUNT(*) FROM passage_fts").fetchone()[0]
        version_row = db.execute(
            "SELECT value FROM app_meta WHERE key='search_index_version'"
        ).fetchone()
        if version_row and version_row[0] == SEARCH_INDEX_VERSION and passage_count == fts_count:
            return
        db.execute("DELETE FROM passage_fts")
        rows = db.execute(
            """
            SELECT p.id, p.text, p.article, d.title
            FROM passages p JOIN documents d ON d.id=p.document_id
            ORDER BY p.document_id, p.position
            """
        ).fetchall()
        db.executemany(
            "INSERT INTO passage_fts(evidence_id,body,title,article) VALUES (?,?,?,?)",
            [
                (
                    row["id"],
                    token_document(row["text"]),
                    token_document(row["title"]),
                    token_document(row["article"] or ""),
                )
                for row in rows
            ],
        )
        db.execute(
            """
            INSERT INTO app_meta(key,value) VALUES('search_index_version',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (SEARCH_INDEX_VERSION,),
        )

    @staticmethod
    def _strip_legacy_metadata(value: Any) -> Any:
        removed = {"sourceLevel", "source_level", "reviewStatus", "review_status", "review_note"}
        if isinstance(value, dict):
            return {
                key: Database._strip_legacy_metadata(item)
                for key, item in value.items()
                if key not in removed
            }
        if isinstance(value, list):
            return [Database._strip_legacy_metadata(item) for item in value]
        return value

    @staticmethod
    def _remove_level_and_review_metadata(db: sqlite3.Connection) -> None:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)").fetchall()}
        legacy_columns = [
            column for column in ("source_level", "review_status", "review_note") if column in columns
        ]
        if not legacy_columns and db.execute("PRAGMA user_version").fetchone()[0] >= 2:
            return
        db.execute("DROP INDEX IF EXISTS idx_documents_status")
        for column in legacy_columns:
            db.execute(f"ALTER TABLE documents DROP COLUMN {column}")
        for row in db.execute("SELECT id, answer_json FROM jobs WHERE answer_json IS NOT NULL").fetchall():
            try:
                answer = json.loads(row["answer_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            cleaned = Database._strip_legacy_metadata(answer)
            db.execute(
                "UPDATE jobs SET answer_json=? WHERE id=?",
                (json.dumps(cleaned, ensure_ascii=False), row["id"]),
            )
        db.execute("PRAGMA user_version = 2")

    def get_document_by_hash(self, digest: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE sha256 = ?", (digest,)).fetchone()
            return dict(row) if row else None

    def delete_document_by_hash(self, digest: str) -> None:
        with self.connect() as db:
            evidence_ids = [
                row[0]
                for row in db.execute(
                    "SELECT p.id FROM passages p JOIN documents d ON d.id=p.document_id WHERE d.sha256=?",
                    (digest,),
                ).fetchall()
            ]
            db.executemany("DELETE FROM passage_fts WHERE evidence_id=?", [(item,) for item in evidence_ids])
            db.execute("DELETE FROM documents WHERE sha256 = ?", (digest,))

    def delete_document(self, document_id: str) -> None:
        with self.connect() as db:
            evidence_ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM passages WHERE document_id=?", (document_id,)
                ).fetchall()
            ]
            db.executemany("DELETE FROM passage_fts WHERE evidence_id=?", [(item,) for item in evidence_ids])
            db.execute("DELETE FROM documents WHERE id=?", (document_id,))

    def update_document_path(self, document_id: str, path: str, filename: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET path=?, filename=?, updated_at=? WHERE id=?",
                (path, filename, utc_now(), document_id),
            )

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
            db.executemany(
                "INSERT INTO passage_fts(evidence_id,body,title,article) VALUES (?,?,?,?)",
                [
                    (
                        item["id"],
                        token_document(item["text"]),
                        token_document(document["title"]),
                        token_document(item.get("article") or ""),
                    )
                    for item in passages
                ],
            )

    def search_passages_fts(
        self,
        match_query: str,
        *,
        as_of_date: str | None,
        include_future_rules: bool,
        limit: int,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [match_query]
        filters = []
        if as_of_date:
            filters.extend(
                [
                    "(d.publication_date IS NULL OR d.publication_date <= ?)",
                    "(d.expiry_date IS NULL OR d.expiry_date >= ?)",
                ]
            )
            params.extend([as_of_date, as_of_date])
            if not include_future_rules:
                filters.append("(d.effective_date IS NULL OR d.effective_date <= ?)")
                params.append(as_of_date)
        if source_filter:
            filters.append("LOWER(d.filename) LIKE ?")
            params.append(f"%{source_filter.lower()}%")
        where = " AND " + " AND ".join(filters) if filters else ""
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT p.*, d.title AS document_title, d.filename,
                       d.official_url, d.publication_date, d.effective_date, d.expiry_date,
                       bm25(passage_fts, 0.0, 1.0, 1.15, 1.1) AS fts_rank
                FROM passage_fts
                JOIN passages p ON p.id=passage_fts.evidence_id
                JOIN documents d ON d.id=p.document_id
                WHERE passage_fts MATCH ? {where}
                ORDER BY fts_rank ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def passage_context(self, evidence_id: str, context_chunks: int) -> list[dict[str, Any]]:
        target = self.get_passage(evidence_id)
        if not target:
            return []
        lower = max(0, target["position"] - context_chunks)
        upper = target["position"] + context_chunks
        article_filter = "p.article IS NULL" if target.get("article") is None else "p.article=?"
        params: list[Any] = [target["document_id"]]
        if target.get("article") is not None:
            params.append(target["article"])
        params.extend([lower, upper])
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT p.*, d.title AS document_title, d.filename, d.official_url,
                       d.publication_date, d.effective_date, d.expiry_date
                FROM passages p JOIN documents d ON d.id=p.document_id
                WHERE p.document_id=? AND {article_filter} AND p.position BETWEEN ? AND ?
                ORDER BY p.position
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def search_index_status(self) -> dict[str, Any]:
        with self.connect() as db:
            documents = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            passages = db.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
            indexed = db.execute("SELECT COUNT(*) FROM passage_fts").fetchone()[0]
            version = db.execute(
                "SELECT value FROM app_meta WHERE key='search_index_version'"
            ).fetchone()
        return {
            "ready": passages == indexed and bool(version),
            "mode": "fts5",
            "version": version[0] if version else None,
            "documents": documents,
            "passages": passages,
            "indexedPassages": indexed,
        }

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

    def searchable_passages(
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
                SELECT p.*, d.title AS document_title, d.filename,
                       d.official_url, d.publication_date, d.effective_date, d.expiry_date
                FROM passages p JOIN documents d ON d.id = p.document_id
                WHERE 1=1 {date_filter}
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_passage(self, evidence_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT p.*, d.title AS document_title, d.filename, d.official_url,
                       d.publication_date, d.effective_date, d.expiry_date
                FROM passages p JOIN documents d ON d.id=p.document_id WHERE p.id=?
                """,
                (evidence_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_job(
        self,
        job_id: str,
        question: str,
        conversation_id: str | None,
        as_of_date: str | None,
        created_by: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO jobs
                (id,question,created_by,conversation_id,as_of_date,status,events_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'[]',?,?)""",
                (job_id, question, created_by, conversation_id, as_of_date, "queued", now, now),
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

    def seed_users(self, users: list[dict[str, str]]) -> None:
        now = utc_now()
        with self.connect() as db:
            db.executemany(
                """
                INSERT OR IGNORE INTO users
                    (id,username,password_hash,role,enabled,created_at,updated_at)
                VALUES (:id,:username,:password_hash,:role,1,:created_at,:updated_at)
                """,
                [{**item, "created_at": now, "updated_at": now} for item in users],
            )

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
            return dict(row) if row else None

    def create_single_session(
        self,
        *,
        session_id: str,
        user_id: str,
        token_hash: str,
        expires_at: str,
        login_ip: str | None = None,
        user_agent: str | None = None,
        browser: str | None = None,
        operating_system: str | None = None,
        device: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                UPDATE auth_sessions
                SET revoked_at=?, revoke_reason='session_replaced'
                WHERE user_id=? AND revoked_at IS NULL
                """,
                (now, user_id),
            )
            db.execute(
                """
                INSERT INTO auth_sessions
                    (id,user_id,token_hash,expires_at,created_at,login_ip,user_agent,
                     browser,operating_system,device,last_seen_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id, user_id, token_hash, expires_at, now, login_ip, user_agent,
                    browser, operating_system, device, now,
                ),
            )

    def resolve_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT s.id AS session_id, s.expires_at, s.revoked_at, s.revoke_reason,
                       s.last_seen_at,
                       u.id AS user_id, u.username, u.role, u.enabled
                FROM auth_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=?
                """,
                (token_hash,),
            ).fetchone()
            return dict(row) if row else None

    def touch_session(self, session_id: str, previous_seen_at: str | None) -> None:
        """Update activity at most once per minute to keep polling read-mostly."""
        now = datetime.now(timezone.utc)
        if previous_seen_at:
            try:
                if now - datetime.fromisoformat(previous_seen_at) < timedelta(seconds=60):
                    return
            except (TypeError, ValueError):
                pass
        with self.connect() as db:
            db.execute(
                "UPDATE auth_sessions SET last_seen_at=? WHERE id=? AND revoked_at IS NULL",
                (now.isoformat(), session_id),
            )

    def revoke_session(self, token_hash: str, reason: str = "logout") -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE auth_sessions SET revoked_at=?, revoke_reason=?
                WHERE token_hash=? AND revoked_at IS NULL
                """,
                (utc_now(), reason, token_hash),
            )

    def revoke_session_by_id(self, session_id: str, reason: str = "admin_revoked") -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """
                UPDATE auth_sessions SET revoked_at=?, revoke_reason=?
                WHERE id=? AND revoked_at IS NULL
                """,
                (utc_now(), reason, session_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _page(page: int, page_size: int) -> tuple[int, int]:
        safe_page = max(1, page)
        safe_size = min(100, max(1, page_size))
        return safe_size, (safe_page - 1) * safe_size

    def monitoring_overview(self, *, day_start: str, day_end: str, online_cutoff: str) -> dict[str, Any]:
        with self.connect() as db:
            online_users = db.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM auth_sessions
                WHERE revoked_at IS NULL AND expires_at>? AND last_seen_at>=?
                """,
                (utc_now(), online_cutoff),
            ).fetchone()[0]
            logins_today = db.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE created_at>=? AND created_at<?",
                (day_start, day_end),
            ).fetchone()[0]
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                       AVG(CASE WHEN status='completed'
                           THEN (julianday(updated_at)-julianday(created_at))*86400 END) AS average_seconds
                FROM jobs WHERE created_at>=? AND created_at<?
                """,
                (day_start, day_end),
            ).fetchone()
        total = row["total"] or 0
        completed = row["completed"] or 0
        return {
            "onlineUsers": online_users,
            "loginsToday": logins_today,
            "questionsToday": total,
            "successRate": round(completed * 100 / total, 1) if total else 0,
            "averageDurationSeconds": round(row["average_seconds"] or 0, 1),
        }

    def monitoring_users(self, *, online_cutoff: str) -> list[dict[str, Any]]:
        now = utc_now()
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT u.id, u.username, u.role, u.enabled,
                       latest.id AS session_id, latest.created_at AS last_login_at,
                       latest.last_seen_at, latest.login_ip, latest.browser,
                       latest.operating_system, latest.device, latest.revoked_at,
                       latest.expires_at,
                       (SELECT COUNT(*) FROM jobs j WHERE j.created_by=u.id) AS question_count
                FROM users u
                LEFT JOIN auth_sessions latest ON latest.id=(
                    SELECT s.id FROM auth_sessions s WHERE s.user_id=u.id
                    ORDER BY s.created_at DESC LIMIT 1
                )
                ORDER BY u.role ASC, u.username COLLATE NOCASE
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["online"] = bool(
                item["enabled"] and not item["revoked_at"]
                and item["expires_at"] and item["expires_at"] > now
                and item["last_seen_at"] and item["last_seen_at"] >= online_cutoff
            )
            result.append(item)
        return result

    def monitoring_sessions(
        self, *, page: int, page_size: int, username: str | None = None,
        status: str | None = None, date_from: str | None = None, date_to: str | None = None,
        online_cutoff: str,
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: list[Any] = []
        if username:
            filters.append("LOWER(u.username) LIKE ?")
            params.append(f"%{username.strip().lower()}%")
        if date_from:
            filters.append("s.created_at>=?")
            params.append(date_from)
        if date_to:
            filters.append("s.created_at<?")
            params.append(date_to)
        now = utc_now()
        if status == "online":
            filters.append("s.revoked_at IS NULL AND s.expires_at>? AND s.last_seen_at>=?")
            params.extend([now, online_cutoff])
        elif status == "active":
            filters.append("s.revoked_at IS NULL AND s.expires_at>?")
            params.append(now)
        elif status == "revoked":
            filters.append("s.revoked_at IS NOT NULL")
        elif status == "expired":
            filters.append("s.revoked_at IS NULL AND s.expires_at<=?")
            params.append(now)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        limit, offset = self._page(page, page_size)
        with self.connect() as db:
            total = db.execute(
                f"SELECT COUNT(*) FROM auth_sessions s JOIN users u ON u.id=s.user_id{where}",
                params,
            ).fetchone()[0]
            rows = db.execute(
                f"""
                SELECT s.id, u.username, u.role, s.created_at, s.last_seen_at,
                       s.expires_at, s.revoked_at, s.revoke_reason, s.login_ip,
                       s.user_agent, s.browser, s.operating_system, s.device
                FROM auth_sessions s JOIN users u ON u.id=s.user_id
                {where} ORDER BY s.created_at DESC LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if item["revoked_at"]:
                item["status"] = "revoked"
            elif item["expires_at"] <= now:
                item["status"] = "expired"
            elif item["last_seen_at"] and item["last_seen_at"] >= online_cutoff:
                item["status"] = "online"
            else:
                item["status"] = "inactive"
            items.append(item)
        return {"items": items, "total": total, "page": page, "pageSize": limit}

    def monitoring_questions(
        self, *, page: int, page_size: int, username: str | None = None,
        status: str | None = None, keyword: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: list[Any] = []
        if username:
            filters.append("LOWER(u.username) LIKE ?")
            params.append(f"%{username.strip().lower()}%")
        if status:
            filters.append("j.status=?")
            params.append(status)
        if keyword:
            filters.append("j.question LIKE ?")
            params.append(f"%{keyword.strip()}%")
        if date_from:
            filters.append("j.created_at>=?")
            params.append(date_from)
        if date_to:
            filters.append("j.created_at<?")
            params.append(date_to)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        limit, offset = self._page(page, page_size)
        join = " FROM jobs j LEFT JOIN users u ON u.id=j.created_by"
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*){join}{where}", params).fetchone()[0]
            rows = db.execute(
                f"""
                SELECT j.id, j.question, j.status, j.error, j.created_at, j.updated_at,
                       u.username,
                       ROUND((julianday(j.updated_at)-julianday(j.created_at))*86400, 1)
                           AS duration_seconds
                {join}{where} ORDER BY j.created_at DESC LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [dict(row) for row in rows], "total": total,
            "page": page, "pageSize": limit,
        }

    def monitoring_question_detail(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT j.*, u.username,
                       ROUND((julianday(j.updated_at)-julianday(j.created_at))*86400, 1)
                           AS duration_seconds
                FROM jobs j LEFT JOIN users u ON u.id=j.created_by WHERE j.id=?
                """,
                (job_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["events"] = json.loads(item.pop("events_json") or "[]")
        item["answer"] = json.loads(item.pop("answer_json")) if item.get("answer_json") else None
        item.pop("answer_json", None)
        return item

    def cleanup_monitoring_data(self, retention_days: int = 30) -> dict[str, int]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self.connect() as db:
            sessions = db.execute(
                "DELETE FROM auth_sessions WHERE created_at<?", (cutoff,)
            ).rowcount
            jobs = db.execute("DELETE FROM jobs WHERE created_at<?", (cutoff,)).rowcount
        return {"sessions": sessions, "jobs": jobs}

    def job_belongs_to(self, job_id: str, user_id: str) -> bool:
        with self.connect() as db:
            return db.execute(
                "SELECT 1 FROM jobs WHERE id=? AND created_by=?", (job_id, user_id)
            ).fetchone() is not None

    @staticmethod
    def _answer_references(answer: Any, key: str, value: str) -> bool:
        if isinstance(answer, dict):
            if answer.get(key) == value:
                return True
            return any(Database._answer_references(item, key, value) for item in answer.values())
        if isinstance(answer, list):
            return any(Database._answer_references(item, key, value) for item in answer)
        return False

    def user_answer_references(self, user_id: str, key: str, value: str) -> bool:
        with self.connect() as db:
            rows = db.execute(
                "SELECT answer_json FROM jobs WHERE created_by=? AND answer_json IS NOT NULL",
                (user_id,),
            ).fetchall()
        for row in rows:
            try:
                answer = json.loads(row["answer_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if self._answer_references(answer, key, value):
                return True
        return False
