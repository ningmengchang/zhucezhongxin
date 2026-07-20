"""Small, non-sensitive real Codex integration smoke test."""

from __future__ import annotations

import asyncio
import json

from .codex_runner import CodexRunner
from .config import settings


async def main() -> None:
    prompt = (
        "只返回符合输出Schema的JSON。status设为insufficient_evidence，"
        "summary写‘结构化输出测试通过’，claims、implementationAdvice、"
        "missingEvidence、searchedSources全部为空。不要使用工具。"
    )
    runner = CodexRunner(settings)
    print(json.dumps(await runner.health(), ensure_ascii=False))
    result = await runner.run(prompt, "legal_answer.schema.json")
    print(json.dumps(result.payload, ensure_ascii=False))
    print(f"events={len(result.events)}")


if __name__ == "__main__":
    asyncio.run(main())

