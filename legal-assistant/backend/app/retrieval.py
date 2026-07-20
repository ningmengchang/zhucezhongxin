from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date

from .database import Database
from .models import Evidence, SourceLevel


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./()-]*|\d+(?:\.\d+)*|[\u4e00-\u9fff]")
LEVEL_BOOST = {"A1": 1.35, "A2": 1.25, "B": 1.0, "C": 0.95, "D": 0.45}
ZH_EN_GLOSSARY = {
    "海关": ["customs", "customs authorities", "customs controls"],
    "查询": ["search", "retrieve", "verify", "verification"],
    "核对": ["verify", "verification", "correspond"],
    "注册中心": ["registry", "digital product passport registry"],
    "登记": ["registration", "register", "registered"],
    "唯一登记标识符": ["unique registration identifier"],
    "唯一标识符": ["unique identifier", "unique identifiers"],
    "电池唯一标识符": ["unique identifier", "batteries"],
    "产品唯一标识符": ["unique product identifier"],
    "商品编码": ["commodity code", "commodity codes"],
    "经济运营商": ["economic operator", "economic operators"],
    "经济运营者": ["economic operator", "economic operators"],
    "服务商": ["service provider", "digital product passport service provider"],
    "提供商": ["service provider"],
    "电池": ["battery", "batteries"],
    "电池护照": ["battery passport"],
    "数字产品护照": ["digital product passport"],
    "护照": ["passport"],
    "二维码": ["QR code"],
    "上传": ["upload", "uploading"],
    "投放市场": ["placing on the market", "placed on the market"],
    "投入使用": ["putting into service", "put into service"],
    "自由流通": ["release for free circulation"],
    "申报": ["declaration", "provide", "make available"],
    "入参": ["data", "identifier", "input", "unique registration identifier", "commodity code"],
    "字段": ["data", "data element", "attribute", "schema"],
    "模型": ["data model", "schema", "semantic definition"],
    "api": ["API", "application programming interface", "publicly documented APIs"],
    "接口": ["API", "application programming interface", "user interface"],
    "责任": ["responsibility", "responsible", "obligation"],
    "职责": ["responsibility", "responsible", "obligation"],
    "义务": ["obligation", "shall"],
    "法规": ["regulation", "union law"],
    "条款": ["article", "paragraph"],
}
CRITICAL_PHRASES = {
    "application programming interface",
    "publicly documented apis",
    "unique registration identifier",
    "unique product identifier",
    "unique identifier",
    "commodity code",
    "economic operator",
    "customs authorities",
    "release for free circulation",
    "digital product passport service provider",
}


def tokenize(text: str) -> list[str]:
    base = [token.lower() for token in TOKEN_RE.findall(text)]
    chinese = [token for token in base if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    bigrams = [chinese[i] + chinese[i + 1] for i in range(len(chinese) - 1)]
    return base + bigrams


def expand_query(query: str) -> str:
    additions: list[str] = []
    lowered = query.lower()
    for chinese, english_terms in ZH_EN_GLOSSARY.items():
        if chinese in lowered:
            additions.extend(english_terms)
    return f"{query} {' '.join(additions)}" if additions else query


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
    """Create deterministic legal-concept queries without an extra model call."""
    lowered = query.lower()
    intent = (routing_text or query).lower()
    variants = [expand_query(query)]
    if "api" in lowered or "接口" in query:
        variants.extend(
            [
                "digital product passport registry application programming interface API registering receiving information",
                "registry secure user interface API response proof of registration",
                "semantic repository publicly documented APIs search service common data formats",
                "Regulation EU 2024 1781 Article 13 digital product passport registry stores unique identifiers Commission",
            ]
        )
    if "入参" in query or "参数" in query or "字段" in query:
        variants.extend(
            [
                "registry data required registration unique identifiers commodity code",
                "economic operator provide information registration schema data elements",
            ]
        )
    if "职责" in query or "责任" in query or "义务" in query or "服务商" in query:
        variants.append(
            "digital product passport service provider obligations responsibilities economic operator access"
        )
    if "海关" in query:
        variants.append(
            "customs authorities verify unique registration identifier commodity code release for free circulation"
        )
    if "模型" in query or "语义" in query:
        variants.append("semantic repository data models semantic definitions vocabularies attributes")
    if ("电池" in intent or "battery" in intent) and (
        "唯一标识" in intent or "identifier" in intent or "护照标识" in intent
    ):
        variants.extend(
            [
                "unique identifier unique string characters identification batteries web link battery passport",
                "Article 77 battery passport QR code links unique identifier economic operator attribute",
                "economic operator upload the unique identifier in the digital product passport registry",
                "unique product identifier identification product web link digital product passport",
                "registry automatically communicate unique registration identifier associated with unique identifiers",
                "battery passport identifier BattReg specifies no requirements",
            ]
        )
    return list(dict.fromkeys(variants))


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

    @staticmethod
    def _score_query(query: str, passages: list[dict], document_tokens: list[list[str]]) -> list[tuple[float, dict]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        doc_frequency: Counter[str] = Counter()
        for tokens in document_tokens:
            doc_frequency.update(set(tokens))
        average_length = sum(len(tokens) for tokens in document_tokens) / max(len(document_tokens), 1)
        total = len(passages)
        results: list[tuple[float, dict]] = []
        normalized_query = " ".join(query.lower().split())

        for item, tokens in zip(passages, document_tokens):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                inverse = math.log(1 + (total - doc_frequency[token] + 0.5) / (doc_frequency[token] + 0.5))
                denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1))
                score += inverse * (frequency * 2.2 / denominator)
            if normalized_query and normalized_query in item["normalized_text"]:
                score += 6.0
            for phrase in CRITICAL_PHRASES:
                if phrase in query.lower() and phrase in item["normalized_text"]:
                    score += 12.0
            score *= LEVEL_BOOST.get(item["source_level"], 0.5)
            if score > 0:
                results.append((score, item))
        return sorted(results, key=lambda pair: pair[0], reverse=True)

    def search(
        self,
        query: str,
        *,
        as_of_date: date | None = None,
        include_future_rules: bool = True,
        context_queries: list[str] | None = None,
        limit: int = 24,
    ) -> list[Evidence]:
        effective_on = as_of_date or date.today()
        passages = self.database.published_passages(
            effective_on.isoformat(), include_future_rules=include_future_rules
        )
        if not passages:
            return []

        document_tokens = [tokenize(item["text"]) for item in passages]
        fused_scores: defaultdict[str, float] = defaultdict(float)
        primary_scores: defaultdict[str, float] = defaultdict(float)
        item_map = {item["id"]: item for item in passages}
        prepared_context = prepare_context_queries(query, context_queries)
        routing_text = " ".join([query, *prepared_context])
        for variant in query_variants(query, routing_text):
            ranked = self._score_query(variant, passages, document_tokens)
            for rank, (raw_score, item) in enumerate(ranked[:80], start=1):
                contribution = 1.0 / (35 + rank) + min(raw_score, 60.0) / 6000
                fused_scores[item["id"]] += contribution
                primary_scores[item["id"]] += contribution
        context_weight = 0.65 if _is_context_dependent(query) else 0.25
        for context_query in prepared_context:
            for variant in query_variants(context_query):
                ranked = self._score_query(variant, passages, document_tokens)
                for rank, (raw_score, item) in enumerate(ranked[:60], start=1):
                    fused_scores[item["id"]] += context_weight * (
                        1.0 / (35 + rank) + min(raw_score, 60.0) / 6000
                    )

        ranked_items = sorted(
            ((score, item_map[evidence_id]) for evidence_id, score in fused_scores.items()),
            key=lambda pair: pair[0],
            reverse=True,
        )

        # Select diverse seed passages so one broad article cannot hide a specific implementing rule.
        selected: list[tuple[float, dict]] = []
        article_counts: Counter[tuple[str, str]] = Counter()
        document_counts: Counter[str] = Counter()
        for score, item in ranked_items:
            article_key = (item["document_id"], item.get("article") or f"page-{item['page']}")
            if article_counts[article_key] >= 3 or document_counts[item["document_id"]] >= 10:
                continue
            selected.append((score, item))
            article_counts[article_key] += 1
            document_counts[item["document_id"]] += 1
            if len(selected) >= limit:
                break

        # Add the nearest passages in the same article to preserve legal context.
        selected_ids = {item["id"] for _, item in selected}
        candidates_by_article: defaultdict[tuple[str, str | None], list[dict]] = defaultdict(list)
        for item in passages:
            candidates_by_article[(item["document_id"], item.get("article"))].append(item)
        for items in candidates_by_article.values():
            items.sort(key=lambda value: value["position"])

        contextual: list[tuple[float, dict]] = list(selected)
        for score, seed in selected[: max(6, limit // 2)]:
            group = candidates_by_article[(seed["document_id"], seed.get("article"))]
            seed_index = next((i for i, item in enumerate(group) if item["id"] == seed["id"]), -1)
            if seed_index < 0:
                continue
            for neighbor_index in (seed_index - 1, seed_index + 1):
                if 0 <= neighbor_index < len(group):
                    neighbor = group[neighbor_index]
                    if neighbor["id"] not in selected_ids:
                        contextual.append((score * 0.86, neighbor))
                        selected_ids.add(neighbor["id"])

        contextual.sort(key=lambda pair: pair[0], reverse=True)
        required: list[tuple[float, dict]] = []
        required_ids: set[str] = set()

        def add_anchor(
            anchors: tuple[str, ...], *, article: str | None = None, prefer_primary: bool = True
        ) -> None:
            matches = [
                item
                for item in passages
                if all(anchor in item["normalized_text"] for anchor in anchors)
                and (article is None or (item.get("article") or "").lower() == article.lower())
            ]
            if not matches:
                return
            best = max(
                matches,
                key=lambda item: (
                    1 if prefer_primary and item["source_level"] in {"A1", "A2"} else 0,
                    fused_scores.get(item["id"], 0.0),
                ),
            )
            if best["id"] not in required_ids:
                required.append((fused_scores.get(best["id"], 0.0), best))
                required_ids.add(best["id"])

        if "api" in query.lower() or "接口" in query:
            anchor_groups = [
                ("an api for registering the digital product passport",),
                ("api response", "unique registration identifier"),
                ("proof of registration", "through the api"),
                ("publicly documented apis", "common data formats"),
                ("semantic repository shall include a search service",),
            ]
            for anchors in anchor_groups:
                add_anchor(anchors)
            current_baseline = [
                item
                for item in passages
                if "Regulation (EU) 2024/1781" in item["document_title"]
                and (item.get("article") or "").lower() == "article 13"
                and "commission shall set up a digital registry" in item["normalized_text"]
            ]
            if current_baseline:
                best = max(current_baseline, key=lambda item: fused_scores.get(item["id"], 0.0))
                if best["id"] not in required_ids:
                    required.append((fused_scores.get(best["id"], 0.0), best))
                    required_ids.add(best["id"])

        battery_identifier_intent = ("电池" in routing_text or "battery" in routing_text.lower()) and (
            "唯一标识" in routing_text or "identifier" in routing_text.lower()
        )
        if battery_identifier_intent:
            add_anchor(
                ("unique string of characters", "identification of batteries", "web link to the battery passport"),
                article="Article 3",
            )
            add_anchor(
                ("battery passport shall be accessible", "qr code", "shall attribute to it"),
                article="Article 77",
            )
            add_anchor(
                ("upload the unique identifier", "registry referred to in article 13"),
                article="Article 77",
            )
            add_anchor(
                ("unique product identifier", "identification of a product", "web link to the digital product passport"),
                article="Article 2",
            )
            add_anchor(
                ("automatically communicate", "unique registration identifier", "associated with the unique identifiers"),
                article="Article 13",
            )
            add_anchor(
                ("api response", "unique registration identifier", "successful submission"),
                article="Article 8",
            )
            add_anchor(
                ("battery passport identifier", "battreg specifies no"),
                prefer_primary=False,
            )

        formal_reserve: list[tuple[float, dict]] = []
        if primary_scores:
            top_primary = max(primary_scores.values())
            reserve_article_counts: Counter[tuple[str, str]] = Counter()
            for evidence_id, score in sorted(primary_scores.items(), key=lambda pair: pair[1], reverse=True):
                item = item_map[evidence_id]
                if item["source_level"] not in {"A1", "A2"} or score < top_primary * 0.35:
                    continue
                article_key = (item["document_id"], item.get("article") or f"page-{item['page']}")
                if reserve_article_counts[article_key] >= 2:
                    continue
                formal_reserve.append((fused_scores[evidence_id], item))
                reserve_article_counts[article_key] += 1
                if len(formal_reserve) >= min(8, max(4, limit // 3)):
                    break

        ordered: list[tuple[float, dict]] = []
        ordered_ids: set[str] = set()
        for pair in required + formal_reserve + contextual:
            if pair[1]["id"] in ordered_ids:
                continue
            ordered.append(pair)
            ordered_ids.add(pair[1]["id"])

        evidence: list[Evidence] = []
        for score, item in ordered[:limit]:
            evidence.append(
                Evidence(
                    evidenceId=item["id"],
                    documentId=item["document_id"],
                    documentTitle=item["document_title"],
                    filename=item["filename"],
                    sourceLevel=SourceLevel(item["source_level"]),
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
