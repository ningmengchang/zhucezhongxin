from datetime import date

from app.models import Citation, Claim, Evidence, LegalAnswer, QuestionRequest, SourceLevel
from app.qa_service import QAService


def evidence(level=SourceLevel.A1):
    return Evidence(
        evidenceId="EV-001",
        documentId="DOC-001",
        documentTitle="Regulation",
        filename="regulation.pdf",
        sourceLevel=level,
        article="Article 15",
        page=43,
        quote="Customs authorities shall verify the unique registration identifier and the commodity code.",
    )


def answer(quote: str, level=SourceLevel.A1):
    return LegalAnswer(
        status="answered",
        summary="summary",
        claims=[
            Claim(
                claimId="C-01",
                text="海关核对登记标识和商品编码。",
                nature="legal_fact",
                supportStatus="supported",
                citations=[
                    Citation(
                        evidenceId="EV-001",
                        documentId="DOC-001",
                        sourceLevel=level,
                        article="Article 15",
                        page=43,
                        quote=quote,
                        interpretation="中文释义",
                    )
                ],
            )
        ],
    )


def test_exact_quote_and_primary_source_pass():
    result = QAService._deterministic_validate(
        answer("the unique registration identifier and the commodity code"), [evidence()]
    )
    assert result.status == "answered"
    assert len(result.claims) == 1


def test_invented_quote_is_blocked():
    result = QAService._deterministic_validate(answer("invented legal obligation"), [evidence()])
    assert result.status == "insufficient_evidence"
    assert result.claims == []
    assert result.missingEvidence


def test_non_primary_source_cannot_support_legal_fact():
    result = QAService._deterministic_validate(
        answer("the unique registration identifier", SourceLevel.B), [evidence(SourceLevel.B)]
    )
    assert result.status == "insufficient_evidence"
    assert result.claims == []


def test_validation_enriches_citation_metadata():
    source = evidence()
    source.documentTitle = "Official Registry Regulation"
    source.filename = "registry.pdf"
    source.effectiveDate = date(2026, 8, 6)
    source.applicabilityStatus = "future"
    result = QAService._deterministic_validate(
        answer("the unique registration identifier"), [source]
    )

    citation = result.claims[0].citations[0]
    assert citation.documentTitle == "Official Registry Regulation"
    assert citation.filename == "registry.pdf"
    assert citation.effectiveDate == date(2026, 8, 6)
    assert citation.applicabilityStatus == "future"


def test_time_context_warns_about_future_primary_rule():
    source = evidence()
    source.documentTitle = "Commission Implementing Regulation (EU) 2026/1778"
    source.effectiveDate = date(2026, 8, 6)
    source.applicabilityStatus = "future"

    context = QAService._time_context(
        QuestionRequest(question="注册中心API", as_of_date=date(2026, 7, 20)), [source]
    )

    assert context.asOfDate == date(2026, 7, 20)
    assert context.notices[0].effectiveDate == date(2026, 8, 6)
    assert "尚未生效" in context.notices[0].note
