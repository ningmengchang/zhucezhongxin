from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from .database import Database
from .retrieval import Retriever


INSTRUCTIONS = (
    "先用SearchLaw定位本地法规。只有ReadLawSource返回的source_id可以作为最终答案引用；"
    "摘要不足时读取相邻条款，不得凭模型记忆补充法规事实。"
)

TOOLS = [
    {
        "name": "SearchLaw",
        "description": "搜索本地法规索引，返回少量候选摘要和source_id。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 400},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 12, "default": 8},
                "source_filter": {"type": "string", "maxLength": 200},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "ReadLawSource",
        "description": "读取SearchLaw已命中的法规原文及同一条款相邻文本。同一source_id每个任务只能读取一次，应复用已返回的原文。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["source_id"],
            "properties": {
                "source_id": {"type": "string", "minLength": 3, "maxLength": 100},
                "context_chunks": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
]


@dataclass
class ToolState:
    retriever: Retriever
    as_of_date: date
    include_future_rules: bool
    trace_path: Path
    max_search_calls: int
    max_read_calls: int
    default_top_k: int = 8
    searched_ids: set[str] = field(default_factory=set)
    read_ids: set[str] = field(default_factory=set)
    search_calls: int = 0
    read_calls: int = 0

    def trace(self, payload: dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _timed_trace(
    state: ToolState,
    payload: dict[str, Any],
    started_at: datetime,
    started_clock: float,
) -> None:
    finished_at = datetime.now(timezone.utc)
    state.trace(
        {
            **payload,
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "durationMs": round((perf_counter() - started_clock) * 1000, 3),
        }
    )


def search_law(
    state: ToolState, query: str, top_k: int | None = None, source_filter: str | None = None
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    started_clock = perf_counter()
    state.search_calls += 1
    if state.search_calls > state.max_search_calls:
        _timed_trace(
            state,
            {
                "action": "search_rejected",
                "query": query,
                "resultIds": [],
                "resultCount": 0,
                "reason": "search_call_limit",
            },
            started_at,
            started_clock,
        )
        return {"error": f"SearchLaw最多调用{state.max_search_calls}次", "results": []}
    bounded_top_k = min(max(int(top_k or state.default_top_k), 1), 12)
    results = state.retriever.search(
        query,
        as_of_date=state.as_of_date,
        include_future_rules=state.include_future_rules,
        limit=bounded_top_k,
        source_filter=source_filter,
    )
    state.searched_ids.update(item.evidenceId for item in results)
    payload = [
        {
            "source_id": item.evidenceId,
            "file": item.filename,
            "title": item.documentTitle,
            "page": item.page,
            "article": item.article,
            "applicability": item.applicabilityStatus,
            "effective_date": item.effectiveDate.isoformat() if item.effectiveDate else None,
            "score": item.score,
            "excerpt": item.quote[:1000],
        }
        for item in results
    ]
    _timed_trace(
        state,
        {
            "action": "search",
            "query": query,
            "sourceFilter": source_filter,
            "resultIds": [item["source_id"] for item in payload],
            "resultCount": len(payload),
        },
        started_at,
        started_clock,
    )
    return {"query": query, "result_count": len(payload), "results": payload}


def read_law_source(
    state: ToolState, source_id: str, context_chunks: int = 1
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    started_clock = perf_counter()
    if source_id not in state.searched_ids:
        _timed_trace(
            state,
            {
                "action": "read_rejected",
                "requestedId": source_id,
                "resultIds": [],
                "resultCount": 0,
                "reason": "source_not_searched",
            },
            started_at,
            started_clock,
        )
        return {"error": "只能读取本任务SearchLaw已经命中的source_id", "passages": []}
    if source_id in state.read_ids:
        _timed_trace(
            state,
            {
                "action": "read_duplicate",
                "requestedId": source_id,
                "resultIds": [],
                "resultCount": 0,
                "reason": "source_already_read",
            },
            started_at,
            started_clock,
        )
        return {
            "already_read": True,
            "message": f"{source_id}已读取；请复用上一次返回的原文，不要再次调用ReadLawSource。",
            "passages": [],
        }
    state.read_calls += 1
    if state.read_calls > state.max_read_calls:
        _timed_trace(
            state,
            {
                "action": "read_rejected",
                "requestedId": source_id,
                "resultIds": [],
                "resultCount": 0,
                "reason": "read_call_limit",
            },
            started_at,
            started_clock,
        )
        return {"error": f"ReadLawSource最多调用{state.max_read_calls}次", "passages": []}
    passages = state.retriever.read_source(
        source_id,
        context_chunks=min(max(int(context_chunks), 0), 3),
        as_of_date=state.as_of_date,
    )
    payload = [
        {
            "source_id": item.evidenceId,
            "target": item.evidenceId == source_id,
            "file": item.filename,
            "title": item.documentTitle,
            "page": item.page,
            "article": item.article,
            "paragraph": item.paragraph,
            "applicability": item.applicabilityStatus,
            "effective_date": item.effectiveDate.isoformat() if item.effectiveDate else None,
            "quote": item.quote,
        }
        for item in passages
    ]
    state.read_ids.add(source_id)
    _timed_trace(
        state,
        {
            "action": "read",
            "requestedId": source_id,
            "contextChunks": context_chunks,
            "resultIds": [item["source_id"] for item in payload],
            "resultCount": len(payload),
        },
        started_at,
        started_clock,
    )
    return {"requested_source_id": source_id, "passages": payload}


def _reply(message_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(message_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}},
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.stdout.flush()


def serve(state: ToolState) -> None:
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        message_id = request.get("id")
        if method == "initialize":
            params = request.get("params") or {}
            _reply(
                message_id,
                {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "law-retrieval", "version": "1.0.0"},
                    "instructions": INSTRUCTIONS,
                },
            )
        elif method in {"notifications/initialized", "notifications/cancelled"}:
            continue
        elif method == "ping":
            _reply(message_id, {})
        elif method == "tools/list":
            _reply(message_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = request.get("params") or {}
            arguments = params.get("arguments") or {}
            try:
                if params.get("name") == "SearchLaw":
                    result = search_law(state, **arguments)
                elif params.get("name") == "ReadLawSource":
                    result = read_law_source(state, **arguments)
                else:
                    _error(message_id, -32601, "未知法规工具")
                    continue
                _reply(
                    message_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                        ],
                        "isError": bool(result.get("error")),
                    },
                )
            except (TypeError, ValueError) as exc:
                _reply(
                    message_id,
                    {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                )
        elif message_id is not None:
            _error(message_id, -32601, f"不支持的方法: {method}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--include-future-rules", choices=("true", "false"), required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--max-search-calls", type=int, default=4)
    parser.add_argument("--max-read-calls", type=int, default=8)
    parser.add_argument("--default-top-k", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = ToolState(
        retriever=Retriever(Database(Path(args.database))),
        as_of_date=date.fromisoformat(args.as_of_date),
        include_future_rules=args.include_future_rules == "true",
        trace_path=Path(args.trace),
        max_search_calls=max(1, args.max_search_calls),
        max_read_calls=max(1, args.max_read_calls),
        default_top_k=min(max(1, args.default_top_k), 12),
    )
    serve(state)


if __name__ == "__main__":
    main()
