from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable

from .config import BACKEND_DIR, Settings


class CodexError(RuntimeError):
    pass


@dataclass
class CodexResult:
    payload: dict[str, Any]
    events: list[dict[str, Any]]
    stderr: str
    law_trace: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    queue_wait_ms: float | None = None


@dataclass(frozen=True)
class LawToolContext:
    database_path: Path
    as_of_date: date
    include_future_rules: bool


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class CodexRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(settings.codex_concurrency)
        self.schema_dir = BACKEND_DIR / "schemas"

    async def health(self) -> dict[str, Any]:
        async def command(*arguments: str) -> tuple[int, str, str]:
            process = await asyncio.create_subprocess_exec(
                self.settings.codex_binary,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            return process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")

        try:
            version_code, version, version_err = await command("--version")
            login_code, login, login_err = await command("login", "status")
            login_summary = login.strip()
            if not login_summary:
                login_lines = [line.strip() for line in login_err.splitlines() if line.strip()]
                login_summary = next(
                    (line for line in reversed(login_lines) if "logged in" in line.lower()),
                    "Authenticated" if login_code == 0 else "Not authenticated",
                )
            return {
                "available": version_code == 0,
                "authenticated": login_code == 0,
                "version": version.strip(),
                "login": login_summary,
                "diagnostic": "\n".join(part.strip() for part in (version_err, login_err) if part.strip()),
                "mode": "codex exec / ChatGPT login",
                "concurrency": self.settings.codex_concurrency,
            }
        except Exception as exc:
            return {
                "available": False,
                "authenticated": False,
                "version": None,
                "login": None,
                "diagnostic": str(exc),
                "mode": "codex exec / ChatGPT login",
                "concurrency": self.settings.codex_concurrency,
            }

    async def run(
        self,
        prompt: str,
        schema_name: str,
        *,
        event_callback: EventCallback | None = None,
        allow_web_search: bool = False,
        law_tools: LawToolContext | None = None,
    ) -> CodexResult:
        if self.settings.codex_mock:
            return self._mock_result(schema_name)
        schema_path = (self.schema_dir / schema_name).resolve()
        if not schema_path.is_file():
            raise CodexError(f"输出Schema不存在: {schema_path}")

        queued_clock = perf_counter()
        async with self.semaphore:
            queue_wait_ms = round((perf_counter() - queued_clock) * 1000, 3)
            started_at = datetime.now(timezone.utc)
            started_clock = perf_counter()
            jobs_dir = self.settings.data_dir / "jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="codex-job-", dir=jobs_dir) as job_dir:
                trace_path = Path(job_dir) / "law-tool-trace.jsonl"
                command = self._build_command(
                    schema_path, job_dir, allow_web_search, law_tools, trace_path
                )
                environment = os.environ.copy()
                environment["NO_COLOR"] = "1"
                # Keep the authenticated CODEX_HOME unchanged, but move transient
                # SQLite/runtime state into the writable per-job directory.
                environment["CODEX_SQLITE_HOME"] = job_dir
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                )
                assert process.stdin and process.stdout and process.stderr
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()

                events: list[dict[str, Any]] = []
                final_text: str | None = None

                async def read_stdout() -> None:
                    nonlocal final_text
                    while line := await process.stdout.readline():
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if not decoded:
                            continue
                        try:
                            event = json.loads(decoded)
                        except json.JSONDecodeError:
                            events.append({"type": "unparsed", "text": decoded})
                            continue
                        events.append(event)
                        if event_callback:
                            await event_callback(event)
                        candidate = self._extract_agent_text(event)
                        if candidate:
                            final_text = candidate

                stdout_task = asyncio.create_task(read_stdout())
                stderr_task = asyncio.create_task(process.stderr.read())
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.settings.codex_timeout_seconds)
                    await stdout_task
                    stderr = (await stderr_task).decode("utf-8", errors="replace")
                except asyncio.TimeoutError as exc:
                    process.kill()
                    await process.wait()
                    stdout_task.cancel()
                    stderr_task.cancel()
                    raise CodexError(f"Codex运行超过{self.settings.codex_timeout_seconds}秒") from exc

                if process.returncode != 0:
                    detail = stderr.strip()[-2000:] or "Codex未返回错误详情"
                    raise CodexError(f"Codex退出码{process.returncode}: {detail}")
                if not final_text:
                    raise CodexError("Codex JSONL中没有最终agent_message")
                try:
                    payload = json.loads(final_text)
                except json.JSONDecodeError as exc:
                    raise CodexError("Codex最终消息不是合法JSON") from exc
                law_trace: list[dict[str, Any]] = []
                if trace_path.is_file():
                    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        try:
                            law_trace.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                finished_at = datetime.now(timezone.utc)
                return CodexResult(
                    payload=payload,
                    events=events,
                    stderr=stderr,
                    law_trace=law_trace,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=round((perf_counter() - started_clock) * 1000, 3),
                    queue_wait_ms=queue_wait_ms,
                )

    def _build_command(
        self,
        schema_path: Path,
        job_dir: str,
        allow_web_search: bool,
        law_tools: LawToolContext | None = None,
        trace_path: Path | None = None,
    ) -> list[str]:
        command = [self.settings.codex_binary]
        if allow_web_search:
            command.append("--search")
        if law_tools:
            if trace_path is None:
                raise CodexError("法规MCP工具缺少trace路径")
            backend_dir = Path(__file__).resolve().parent.parent
            args = [
                "-m",
                "app.law_mcp_server",
                "--database",
                str(law_tools.database_path.resolve()),
                "--as-of-date",
                law_tools.as_of_date.isoformat(),
                "--include-future-rules",
                "true" if law_tools.include_future_rules else "false",
                "--trace",
                str(trace_path.resolve()),
                "--default-top-k",
                str(self.settings.law_search_top_k),
            ]
            command.extend(
                [
                    "-c",
                    f"mcp_servers.law.command={json.dumps(sys.executable)}",
                    "-c",
                    f"mcp_servers.law.args={json.dumps(args)}",
                    "-c",
                    f"mcp_servers.law.cwd={json.dumps(str(backend_dir))}",
                    "-c",
                    "mcp_servers.law.required=true",
                    "-c",
                    'mcp_servers.law.enabled_tools=["SearchLaw","ReadLawSource"]',
                    "-c",
                    'mcp_servers.law.default_tools_approval_mode="approve"',
                ]
            )
        command.extend(
            [
                "--ask-for-approval",
                "never",
                "exec",
                "--json",
                "--output-schema",
                str(schema_path),
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--cd",
                job_dir,
                "-",
            ]
        )
        return command

    @staticmethod
    def _extract_agent_text(event: dict[str, Any]) -> str | None:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            return text if isinstance(text, str) else None
        if event.get("type") in {"message", "response.completed"}:
            for key in ("text", "output_text", "response"):
                value = event.get(key)
                if isinstance(value, str):
                    return value
        return None

    @staticmethod
    def _mock_result(_schema_name: str) -> CodexResult:
        payload = {
            "status": "unresolved",
            "markdown": "Mock模式未调用Codex。",
        }
        return CodexResult(payload=payload, events=[{"type": "mock"}], stderr="")
