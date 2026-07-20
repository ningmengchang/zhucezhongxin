"""End-to-end Q&A smoke test against the seeded local regulation library."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from .codex_runner import CodexRunner
from .config import settings
from .database import Database
from .models import QuestionRequest
from .qa_service import QAService
from .retrieval import Retriever


async def main() -> None:
    database = Database(settings.database_path)
    service = QAService(database, Retriever(database), CodexRunner(settings), settings.queue_limit)
    await service.start(1)
    try:
        job_id = await service.submit(
            QuestionRequest(
                question="海关查询注册中心是根据什么入参查的，经济运营商需要提供什么入参？",
                as_of_date=date(2026, 8, 7),
            )
        )
        for _ in range(360):
            job = database.get_job(job_id)
            if job and job["status"] in {"completed", "failed"}:
                print(json.dumps(job, ensure_ascii=False, indent=2))
                if job["status"] == "failed":
                    raise SystemExit(1)
                return
            await asyncio.sleep(0.5)
        raise TimeoutError("End-to-end Q&A smoke test timed out")
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main())

