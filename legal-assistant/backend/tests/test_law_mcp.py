import json
from datetime import date
from pathlib import Path

from app.database import Database
from app.ingestion import ingest_document
from app.law_mcp_server import ToolState, read_law_source, search_law
from app.models import DocumentCreate
from app.retrieval import Retriever


def _state(tmp_path: Path) -> ToolState:
    source = tmp_path / "registry.txt"
    source.write_text(
        "Article 15\nCustoms authorities shall verify the unique registration identifier and commodity code.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "law.sqlite3")
    database.init()
    ingest_document(
        database,
        DocumentCreate(path=str(source), effective_date=date(2020, 1, 1)),
        tmp_path,
    )
    return ToolState(
        retriever=Retriever(database),
        as_of_date=date(2026, 7, 20),
        include_future_rules=True,
        trace_path=tmp_path / "trace.jsonl",
    )


def test_search_then_read_source_records_citeable_passages(tmp_path: Path):
    state = _state(tmp_path)

    found = search_law(state, "customs unique registration identifier commodity code", top_k=3)
    source_id = found["results"][0]["source_id"]
    read = read_law_source(state, source_id, context_chunks=1)

    assert "Customs authorities" in read["passages"][0]["quote"]
    trace = [json.loads(line) for line in state.trace_path.read_text(encoding="utf-8").splitlines()]
    assert [item["action"] for item in trace] == ["search", "read"]
    assert source_id in trace[1]["resultIds"]
    assert all(item["startedAt"] and item["finishedAt"] for item in trace)
    assert all(item["durationMs"] >= 0 for item in trace)


def test_duplicate_read_is_allowed_and_recorded(tmp_path: Path):
    state = _state(tmp_path)
    found = search_law(state, "customs unique registration identifier", top_k=3)
    source_id = found["results"][0]["source_id"]

    first = read_law_source(state, source_id)
    duplicate = read_law_source(state, source_id)

    assert first["passages"]
    assert duplicate["passages"]
    trace = [json.loads(line) for line in state.trace_path.read_text(encoding="utf-8").splitlines()]
    assert [item["action"] for item in trace] == ["search", "read", "read"]


def test_read_does_not_require_a_prior_search(tmp_path: Path):
    state = _state(tmp_path)
    source_id = state.retriever.database.searchable_passages()[0]["id"]

    result = read_law_source(state, source_id)

    assert result["passages"]
    trace = [json.loads(line) for line in state.trace_path.read_text(encoding="utf-8").splitlines()]
    assert [item["action"] for item in trace] == ["read"]
