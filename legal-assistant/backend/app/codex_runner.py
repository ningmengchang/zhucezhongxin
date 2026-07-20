from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import BACKEND_DIR, Settings


class CodexError(RuntimeError):
    pass


@dataclass
class CodexResult:
    payload: dict[str, Any]
    events: list[dict[str, Any]]
    stderr: str


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
    ) -> CodexResult:
        if self.settings.codex_mock:
            return self._mock_result(schema_name)
        schema_path = (self.schema_dir / schema_name).resolve()
        if not schema_path.is_file():
            raise CodexError(f"输出Schema不存在: {schema_path}")

        async with self.semaphore:
            jobs_dir = self.settings.data_dir / "jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="codex-job-", dir=jobs_dir) as job_dir:
                command = [
                    self.settings.codex_binary,
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
                return CodexResult(payload=payload, events=events, stderr=stderr)

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
    def _mock_result(schema_name: str) -> CodexResult:
        if "verification" in schema_name:
            payload = {
                "overall": "pass",
                "coverageStatus": "complete",
                "results": [],
                "revisedSummary": "Mock模式未调用Codex。",
                "supplementalClaims": [],
                "coverageGaps": [],
            }
        else:
            payload = {
                "status": "insufficient_evidence",
                "summary": "Mock模式未调用Codex。",
                "claims": [],
                "implementationAdvice": [],
                "missingEvidence": ["Mock模式"],
                "searchedSources": [],
            }
        return CodexResult(payload=payload, events=[{"type": "mock"}], stderr="")
