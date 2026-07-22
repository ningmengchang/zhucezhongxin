from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date

from .database import Database
from .models import Evidence
from .search_index import fts_query, tokenize


def _query_key(query: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", query.lower())
    return normalized.replace("标识符", "标识").replace("经济运营者", "经济运营商")


def _query_similarity(left: str, right: str) -> float:
    left_key, right_key = _query_key(left), _query_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    left_parts = {left_key[i : i + 2] for i in range(max(len(left_key) - 1, 1))}
    right_parts = {right_key[i : i + 2] for i in range(max(len(right_key) - 1, 1))}
    return len(left_parts & right_parts) / max(len(left_parts | right_parts), 1)


def prepare_context_queries(query: str, context_queries: list[str] | None) -> list[str]:
    selected: list[str] = []
    for candidate in reversed(context_queries or []):
        candidate = candidate.strip()
        if not candidate or _query_similarity(query, candidate) >= 0.78:
            continue
        if any(_query_similarity(candidate, existing) >= 0.78 for existing in selected):
            continue
        selected.append(candidate[:400])
        if len(selected) >= 2:
            break
    return list(reversed(selected))


def _is_context_dependent(query: str) -> bool:
    markers = ("这个", "那个", "上述", "前面", "上一个", "二者", "两者", "它", "其", "呢", "继续")
    compact = _query_key(query)
    return any(marker in query for marker in markers) or len(compact) <= 8


def query_variants(query: str, routing_text: str | None = None) -> list[str]:
    """Search exactly what the agent requested; terminology choices belong to the agent."""
    normalized = " ".join(query.split())
    return [normalized] if normalized else []


def _applicability(item: dict, effective_on: date) -> str:
    effective_date = item.get("effective_date")
    if effective_date and effective_date > effective_on.isoformat():
        return "future"
    if effective_date:
        return "current"
    return "unknown"


class Retriever:
    def __init__(self, database: Database):
        self.database = database

    def search(
        self,
        query: str,
        *,
        as_of_date: date | None = None,
        include_future_rules: bool = True,
        context_queries: list[str] | None = None,
        limit: int = 24,
        source_filter: str | None = None,
    ) -> list[Evidence]:
        effective_on = as_of_date or date.today()
        fused_scores: defaultdict[str, float] = defaultdict(float)
        item_map: dict[str, dict] = {}
        prepared_context = prepare_context_queries(query, context_queries)
        for variant in query_variants(query):
            match = fts_query(variant)
            if not match:
                continue
            ranked = self.database.search_passages_fts(
                match,
                as_of_date=effective_on.isoformat(),
                include_future_rules=include_future_rules,
                limit=80,
                source_filter=source_filter,
            )
            for rank, item in enumerate(ranked, start=1):
                item_map[item["id"]] = item
                contribution = 1.0 / (35 + rank)
                normalized_query = " ".join(variant.lower().split())
                if normalized_query and normalized_query in item["normalized_text"]:
                    contribution += 0.03
                fused_scores[item["id"]] += contribution
        context_weight = 0.65 if _is_context_dependent(query) else 0.25
        for context_query in prepared_context:
            for variant in query_variants(context_query):
                match = fts_query(variant)
                if not match:
                    continue
                ranked = self.database.search_passages_fts(
                    match,
                    as_of_date=effective_on.isoformat(),
                    include_future_rules=include_future_rules,
                    limit=60,
                    source_filter=source_filter,
                )
                for rank, item in enumerate(ranked, start=1):
                    item_map[item["id"]] = item
                    fused_scores[item["id"]] += context_weight / (35 + rank)

        ranked_items = sorted(
            ((score, item_map[evidence_id]) for evidence_id, score in fused_scores.items()),
            key=lambda pair: pair[0],
            reverse=True,
        )

        # Keep the first-stage result compact and diverse; full context is read on demand.
        selected: list[tuple[float, dict]] = []
        article_counts: Counter[tuple[str, str]] = Counter()
        document_counts: Counter[str] = Counter()
        for score, item in ranked_items:
            article_key = (item["document_id"], item.get("article") or f"page-{item['page']}")
            if article_counts[article_key] >= 3 or document_counts[item["document_id"]] >= 8:
                continue
            selected.append((score, item))
            article_counts[article_key] += 1
            document_counts[item["document_id"]] += 1
            if len(selected) >= limit:
                break

        evidence: list[Evidence] = []
        for score, item in selected[:limit]:
            evidence.append(
                Evidence(
                    evidenceId=item["id"],
                    documentId=item["document_id"],
                    documentTitle=item["document_title"],
                    filename=item["filename"],
                    article=item.get("article"),
                    paragraph=item.get("paragraph"),
                    page=item["page"],
                    quote=item["text"],
                    officialUrl=item.get("official_url"),
                    publicationDate=item.get("publication_date"),
                    effectiveDate=item.get("effective_date"),
                    expiryDate=item.get("expiry_date"),
                    applicabilityStatus=_applicability(item, effective_on),
                    score=round(score, 6),
                )
            )
        return evidence

    def read_source(
        self,
        source_id: str,
        *,
        context_chunks: int = 1,
        as_of_date: date | None = None,
    ) -> list[Evidence]:
        effective_on = as_of_date or date.today()
        rows = self.database.passage_context(source_id, min(max(context_chunks, 0), 3))
        return [
            Evidence(
                evidenceId=item["id"],
                documentId=item["document_id"],
                documentTitle=item["document_title"],
                filename=item["filename"],
                article=item.get("article"),
                paragraph=item.get("paragraph"),
                page=item["page"],
                quote=item["text"],
                officialUrl=item.get("official_url"),
                publicationDate=item.get("publication_date"),
                effectiveDate=item.get("effective_date"),
                expiryDate=item.get("expiry_date"),
                applicabilityStatus=_applicability(item, effective_on),
                score=1.0 if item["id"] == source_id else 0.86,
            )
            for item in rows
        ]
