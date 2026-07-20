from app.codex_runner import CodexRunner


def test_extract_agent_message():
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": "{\"ok\":true}"}}
    assert CodexRunner._extract_agent_text(event) == '{"ok":true}'


def test_ignore_non_agent_event():
    assert CodexRunner._extract_agent_text({"type": "turn.started"}) is None

