import asyncio
from datetime import datetime, timezone

import pytest

from app.codex_runner import CodexRunner
from app.config import Settings
from app.models import (
    ReportParagraph,
    ReportSection,
    ReportSource,
    ResearchReport,
    ServiceProviderItem,
)
from app.qa_service import QAService
from app.web_evidence import WebEvidenceValidator


def local_source() -> ReportSource:
    return ReportSource(
        sourceId="EV-001",
        origin="local",
        authorityKind="legal_act",
        title="Regulation (EU) 2024/1781",
        documentId="DOC-001",
        filename="regulation.pdf",
        article="Article 13",
        page=20,
        quote="The Commission shall set up a digital registry storing unique identifiers.",
    )


def report(source: ReportSource) -> ResearchReport:
    return ResearchReport(
        status="answered",
        executiveSummary=[
            ReportParagraph(
                paragraphId="P-01",
                text="欧盟委员会应建立数字产品护照注册中心。",
                nature="legal_analysis",
                sourceRefs=[source.sourceId],
            )
        ],
        analysisSections=[
            ReportSection(
                title="注册中心规则",
                paragraphs=[
                    ReportParagraph(
                        paragraphId="P-02",
                        text="注册中心保存唯一标识符。",
                        nature="process_explanation",
                        sourceRefs=[source.sourceId],
                    )
                ],
            )
        ],
        serviceProviderImpact=[
            ServiceProviderItem(
                itemId="SP-01",
                title="建立登记对接能力",
                detail="这是根据经济运营商流程形成的系统实施建议。",
                nature="derived_implementation_action",
                sourceRefs=[source.sourceId],
            )
        ],
        sources=[source],
    )


def test_report_keeps_verified_primary_local_source():
    result = QAService._validate_report(report(local_source()))
    assert result.status == "answered"
    assert result.executiveSummary[0].sourceRefs == ["EV-001"]
    assert result.sources[0].origin == "local"


def test_report_accepts_any_local_file_as_direct_evidence():
    source = local_source()
    source.authorityKind = "local_supplement"
    result = QAService._validate_report(report(source))
    assert result.status == "answered"
    assert result.executiveSummary[0].sourceRefs == ["EV-001"]


def test_web_validator_accepts_exact_quote_from_official_page(monkeypatch):
    validator = WebEvidenceValidator(("europa.eu",))
    monkeypatch.setattr("app.web_evidence._public_addresses", lambda _: True)

    async def fake_fetch(url: str):
        return url, b"<html>The Commission shall set up a digital registry storing unique identifiers.</html>", "text/html"

    monkeypatch.setattr(validator, "_fetch", fake_fetch)
    source, reason = asyncio.run(
        validator._validate(
            {
                "title": "Official regulation",
                "url": "https://eur-lex.europa.eu/example",
                "publisher": "EUR-Lex",
                "publicationDate": "2024-06-28",
                "effectiveDate": None,
                "authorityKind": "legal_act",
                "quote": "The Commission shall set up a digital registry storing unique identifiers.",
            },
            1,
        )
    )
    assert reason == ""
    assert source and source.origin == "web" and source.verified
    assert source.accessedAt <= datetime.now(timezone.utc)


def test_web_validator_rejects_non_official_domain():
    validator = WebEvidenceValidator(("europa.eu",))
    source, reason = asyncio.run(
        validator._validate(
            {
                "title": "Third party",
                "url": "https://example.com/article",
                "quote": "This is a sufficiently long but untrusted secondary quotation.",
            },
            1,
        )
    )
    assert source is None
    assert "官方域名" in reason


def test_codex_web_search_flag_is_only_added_for_web_round(tmp_path):
    runner = CodexRunner(Settings(data_dir=tmp_path))
    schema = tmp_path / "schema.json"
    local = runner._build_command(schema, str(tmp_path), False)
    web = runner._build_command(schema, str(tmp_path), True)
    assert "--search" not in local
    assert web[1] == "--search"
    assert web.index("--search") < web.index("exec")
