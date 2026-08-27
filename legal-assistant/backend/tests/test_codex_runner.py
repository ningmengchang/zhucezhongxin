import asyncio
import json
from datetime import date
from pathlib import Path

from app.codex_runner import CodexRunner, LawToolContext, _ActiveTurn
from app.config import Settings


def test_app_server_command_uses_stdio_without_exec(tmp_path: Path):
    runner = CodexRunner(Settings(data_dir=tmp_path, database_path=tmp_path / "law.sqlite3"))
    command = runner._build_server_command()

    assert command[1:3] == ["app-server", "--stdio"]
    assert "exec" not in command
    assert "mcp_servers={}" in command
    assert "features.apps=false" in command
    assert "features.plugins=false" in command
    assert "features.multi_agent=false" in command
    assert "features.shell_tool=false" in command
    assert "apps._default.enabled=false" in command


def test_thread_is_ephemeral_and_exposes_only_law_tools_plus_web(tmp_path: Path):
    runner = CodexRunner(Settings(data_dir=tmp_path, database_path=tmp_path / "law.sqlite3"))
    runner._runtime_dir = tmp_path
    (tmp_path / "workspace").mkdir()
    params = runner._thread_start_params(
        LawToolContext(tmp_path / "law.sqlite3", date(2026, 7, 20), True),
        tmp_path / "trace.jsonl",
        True,
    )

    assert params["ephemeral"] is True
    assert params["approvalPolicy"] == "never"
    assert params["sandbox"] == "read-only"
    assert params["personality"] == "none"
    assert "SearchLaw, ReadLawSource, and Web Search" in params["baseInstructions"]
    assert "run shell commands" in params["baseInstructions"]
    assert params["config"]["web_search"] == "live"
    assert set(params["config"]["mcp_servers"]) == {"law"}
    law = params["config"]["mcp_servers"]["law"]
    assert law["required"] is True
    assert law["enabled_tools"] == ["SearchLaw", "ReadLawSource"]
    assert "--as-of-date" in law["args"]
    assert "2026-07-20" in law["args"]


def test_app_server_notifications_capture_final_message_and_completion(tmp_path: Path):
    async def scenario():
        runner = CodexRunner(Settings(data_dir=tmp_path, database_path=tmp_path / "law.sqlite3"))
        completed = asyncio.get_running_loop().create_future()
        active = _ActiveTurn(thread_id="thr-1", completed=completed)
        runner._active_turns["thr-1"] = active

        await runner._dispatch_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "msg-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": '{"status":"answered"}',
                    },
                },
            }
        )
        await runner._dispatch_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thr-1",
                    "turn": {"id": "turn-1", "status": "completed", "items": []},
                },
            }
        )

        assert active.final_text == '{"status":"answered"}'
        assert (await completed)["status"] == "completed"
        assert [item["type"] for item in active.events] == [
            "item/completed",
            "turn/completed",
        ]

    asyncio.run(scenario())


def test_conversation_reuses_thread_then_rolls_over_at_turn_limit(tmp_path: Path):
    async def scenario():
        settings = Settings(
            data_dir=tmp_path,
            database_path=tmp_path / "law.sqlite3",
            codex_conversation_max_turns=2,
        )
        runner = CodexRunner(settings)
        runner._runtime_dir = tmp_path
        (tmp_path / "workspace").mkdir()
        (tmp_path / "traces").mkdir()
        starts = []

        async def request(method, params=None, *, timeout):
            if method == "thread/start":
                thread_id = f"thr-{len(starts) + 1}"
                starts.append(params)
                return {"thread": {"id": thread_id}}
            if method == "mcpServerStatus/list":
                return {
                    "data": [
                        {
                            "name": "law",
                            "tools": {
                                "SearchLaw": {"name": "SearchLaw"},
                                "ReadLawSource": {"name": "ReadLawSource"},
                            },
                        }
                    ]
                }
            return {}

        runner._request_raw = request
        context = LawToolContext(tmp_path / "law.sqlite3", date(2026, 7, 20), True)
        first = await runner._conversation_for("session-1", context, True)
        same = await runner._conversation_for("session-1", context, True)
        assert same.thread_id == first.thread_id

        same.turn_count = 2
        replacement = await runner._conversation_for("session-1", context, True)
        assert replacement.thread_id != first.thread_id
        assert len(starts) == 2

        web_disabled = await runner._conversation_for("session-1", context, False)
        assert web_disabled.thread_id != replacement.thread_id
        assert len(starts) == 3

    asyncio.run(scenario())


def test_trace_reader_returns_only_bytes_appended_after_offset(tmp_path: Path):
    path = tmp_path / "trace.jsonl"
    first = json.dumps({"action": "search", "query": "old"}) + "\n"
    path.write_text(first, encoding="utf-8")
    offset = path.stat().st_size
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"action": "read", "requestedId": "EV-1"}) + "\n")

    assert CodexRunner._read_trace(path, offset) == [
        {"action": "read", "requestedId": "EV-1"}
    ]


def test_close_session_deletes_ephemeral_app_server_thread(tmp_path: Path):
    async def scenario():
        runner = CodexRunner(Settings(data_dir=tmp_path, database_path=tmp_path / "law.sqlite3"))
        runner._initialized = True
        runner._process = type(
            "Process",
            (),
            {"returncode": None, "stdin": object()},
        )()
        runner._runtime_dir = tmp_path
        (tmp_path / "traces").mkdir()
        calls = []
        context = LawToolContext(tmp_path / "law.sqlite3", date(2026, 7, 20), True)

        async def start_request(method, params=None, *, timeout):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "thr-delete"}}
            if method == "mcpServerStatus/list":
                return {
                    "data": [
                        {
                            "name": "law",
                            "tools": {
                                "SearchLaw": {},
                                "ReadLawSource": {},
                            },
                        }
                    ]
                }
            return {}

        runner._request_raw = start_request
        await runner._conversation_for("session-delete", context, True)
        await runner.close_session("session-delete")

        assert ("thread/delete", {"threadId": "thr-delete"}) in calls
        assert "session-delete" not in runner._conversations

    asyncio.run(scenario())


def test_full_stdio_app_server_round_trip_without_network(tmp_path: Path, monkeypatch):
    fake_server = tmp_path / "fake-codex"
    fake_server.write_text(
        """#!/usr/bin/env python3
import json
import sys

def send(message):
    print(json.dumps(message, ensure_ascii=False), flush=True)

for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        send({"id": request_id, "result": {"userAgent": "fake-codex/1.0"}})
    elif method == "account/read":
        send({"id": request_id, "result": {"account": {"type": "chatgpt", "planType": "test"}}})
    elif method == "thread/start":
        send({"id": request_id, "result": {"thread": {"id": "thr-round-trip"}}})
    elif method == "mcpServerStatus/list":
        send({"id": request_id, "result": {"data": [{"name": "law", "tools": {"SearchLaw": {}, "ReadLawSource": {}}}]}})
    elif method == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "turn-round-trip", "status": "inProgress", "items": []}}})
        send({"method": "item/completed", "params": {"threadId": "thr-round-trip", "turnId": "turn-round-trip", "item": {"id": "answer", "type": "agentMessage", "phase": "final_answer", "text": "{\\\"status\\\":\\\"answered\\\",\\\"markdown\\\":\\\"测试答复\\\",\\\"webSources\\\":[]}"}}})
        send({"method": "turn/completed", "params": {"threadId": "thr-round-trip", "turn": {"id": "turn-round-trip", "status": "completed", "items": []}}})
    elif method in {"thread/delete", "thread/unsubscribe", "turn/interrupt"}:
        send({"id": request_id, "result": {}})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": method}})
""",
        encoding="utf-8",
    )
    fake_server.chmod(0o755)
    empty_codex_home = tmp_path / "empty-codex-home"
    empty_codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(empty_codex_home))

    async def scenario():
        settings = Settings(
            data_dir=tmp_path / "data",
            database_path=tmp_path / "law.sqlite3",
            codex_binary=str(fake_server),
            codex_timeout_seconds=2,
            codex_app_server_start_timeout_seconds=2,
        )
        runner = CodexRunner(settings)
        try:
            result = await runner.run(
                "请回答测试问题",
                "markdown_answer.schema.json",
                allow_web_search=True,
                law_tools=LawToolContext(settings.database_path, date(2026, 7, 20), True),
                session_key="session-round-trip",
            )
            assert result.payload == {
                "status": "answered",
                "markdown": "测试答复",
                "webSources": [],
            }
            assert [event["type"] for event in result.events] == [
                "item/completed",
                "turn/completed",
            ]
            assert (await runner.health())["authenticated"] is True
        finally:
            await runner.stop()

    asyncio.run(scenario())
