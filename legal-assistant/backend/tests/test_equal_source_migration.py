import json
import sqlite3

from app.database import Database
from app.retrieval import Retriever, tokenize


def test_legacy_levels_and_review_states_are_permanently_removed(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE, source_level TEXT NOT NULL, review_status TEXT NOT NULL,
            jurisdiction TEXT NOT NULL, authority TEXT, official_url TEXT, publication_date TEXT,
            effective_date TEXT, expiry_date TEXT, version_of TEXT, review_note TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE passages (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            article TEXT, paragraph TEXT, page INTEGER NOT NULL, text TEXT NOT NULL,
            normalized_text TEXT NOT NULL, position INTEGER NOT NULL
        );
        CREATE INDEX idx_documents_status ON documents(review_status, source_level);
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, conversation_id TEXT, as_of_date TEXT,
            status TEXT NOT NULL, answer_json TEXT, error TEXT,
            events_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "DOC-DRAFT", "Draft registry rule", "draft.txt", "/tmp/draft.txt", "hash-draft",
            "D", "disabled", "EU", "Draft", None, None, None, None, None, "legacy note",
            "2026-01-01", "2026-01-01",
        ),
    )
    connection.execute(
        "INSERT INTO passages VALUES (?,?,?,?,?,?,?,?)",
        ("EV-DRAFT", "DOC-DRAFT", "Article 1", None, 1, "Draft registry requirement", "draft registry requirement", 0),
    )
    connection.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "JOB-1", "question", None, None, "completed",
            json.dumps({"schemaVersion": "4.0", "sourceLevel": "D", "nested": {"review_status": "disabled"}}),
            None, "[]", "2026-01-01", "2026-01-01",
        ),
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.init()
    database.init()

    with database.connect() as migrated:
        columns = {row["name"] for row in migrated.execute("PRAGMA table_info(documents)")}
        answer = json.loads(migrated.execute("SELECT answer_json FROM jobs WHERE id='JOB-1'").fetchone()[0])
        version = migrated.execute("PRAGMA user_version").fetchone()[0]
    assert {"source_level", "review_status", "review_note"}.isdisjoint(columns)
    assert "sourceLevel" not in answer and "review_status" not in answer["nested"]
    assert version == 2
    assert Retriever(database).search("draft registry requirement")[0].evidenceId == "EV-DRAFT"


def test_retrieval_score_ignores_legacy_level_values():
    formal = {"normalized_text": "registry requirement", "source_level": "A1"}
    draft = {"normalized_text": "registry requirement", "source_level": "D"}

    assert tokenize(formal["normalized_text"]) == tokenize(draft["normalized_text"])
