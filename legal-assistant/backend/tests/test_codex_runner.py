from datetime import date
from pathlib import Path

from app.codex_runner import CodexRunner, LawToolContext
from app.config import Settings


def test_extract_agent_message():
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": "{\"ok\":true}"}}
    assert CodexRunner._extract_agent_text(event) == '{"ok":true}'


def test_ignore_non_agent_event():
    assert CodexRunner._extract_agent_text({"type": "turn.started"}) is None


def test_law_tools_are_attached_as_required_ephemeral_mcp(tmp_path: Path):
    runner = CodexRunner(Settings(data_dir=tmp_path, database_path=tmp_path / "law.sqlite3"))
    command = runner._build_command(
        tmp_path / "schema.json",
        str(tmp_path),
        False,
        LawToolContext(tmp_path / "law.sqlite3", date(2026, 7, 20), True),
        tmp_path / "trace.jsonl",
    )
    rendered = " ".join(command)

    assert "mcp_servers.law.required=true" in command
    assert "app.law_mcp_server" in rendered
    assert "SearchLaw" in rendered and "ReadLawSource" in rendered
    assert "--sandbox" in command and "read-only" in command
