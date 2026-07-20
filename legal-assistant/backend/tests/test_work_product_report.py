from pathlib import Path

from app.codex_runner import CodexRunner
from app.config import Settings
from app.models import (
    ReportSource,
    ReportStatementV4,
    SourceLevel,
    WorkProductBlock,
    WorkProductItem,
    WorkProductReport,
)
from app.qa_service import QAService


def source(level: SourceLevel = SourceLevel.A1) -> ReportSource:
    return ReportSource(
        sourceId=f"EV-{level.value}",
        origin="local",
        authorityKind="legal_act" if level in {SourceLevel.A1, SourceLevel.A2} else "local_supplement",
        title="Test source",
        sourceLevel=level,
        documentId="DOC-1",
        filename="source.pdf",
        page=1,
        quote="The economic operator shall upload the unique identifier.",
    )


def test_engineering_recommendation_without_source_is_preserved():
    answer = WorkProductReport(
        status="answered",
        answerTitle="字段建议",
        executiveAnswer=[
            ReportStatementV4(
                statementId="E-1",
                text="建议将产品标识和登记标识分字段保存，该方案不是法规强制格式。",
                basis="engineering_recommendation",
                sourceRefs=[],
            )
        ],
    )
    result = QAService._validate_work_product(answer)
    assert result.executiveAnswer
    assert result.status == "answered"
    assert result.qualityNotes == []


def test_b_level_source_cannot_support_regulatory_requirement_and_note_stays_internal():
    b_source = source(SourceLevel.B)
    answer = WorkProductReport(
        status="answered",
        answerTitle="错误法规结论",
        executiveAnswer=[
            ReportStatementV4(
                statementId="E-1",
                text="这是法定要求。",
                basis="regulatory_requirement",
                sourceRefs=[b_source.sourceId],
            )
        ],
        sources=[b_source],
    )
    result = QAService._validate_work_product(answer)
    assert result.executiveAnswer == []
    assert result.status == "unresolved"
    assert result.qualityNotes and result.qualityNotes[0].type == "removed"
    assert result.unresolvedQuestions == []


def test_complex_work_product_quality_gate_requires_useful_blocks(tmp_path: Path):
    runner = CodexRunner(Settings(data_dir=tmp_path, report_complex_min_chars=3000))
    service = QAService(None, None, runner, 1)  # type: ignore[arg-type]
    answer = WorkProductReport(
        status="answered",
        answerTitle="编码规则",
        executiveAnswer=[
            ReportStatementV4(
                statementId="E-1",
                text="法规要求使用唯一标识符。",
                basis="engineering_recommendation",
                sourceRefs=[],
            )
        ],
    )
    gaps = service._work_product_quality_gaps(
        answer,
        {
            "complexity": "complex",
            "intents": ["technical_design"],
            "requiredBlockTypes": ["field_matrix", "example", "checklist"],
        },
    )
    assert any("3000字" in gap for gap in gaps)
    assert any("field_matrix" in gap for gap in gaps)
    assert any("服务商" in gap for gap in gaps)


def test_verifier_reclassifies_instead_of_deleting_useful_item():
    a_source = source()
    answer = WorkProductReport(
        status="answered",
        answerTitle="实施建议",
        executiveAnswer=[
            ReportStatementV4(
                statementId="E-1",
                text="建议保留标准版本字段。",
                basis="regulatory_requirement",
                sourceRefs=[a_source.sourceId],
            )
        ],
        sources=[a_source],
    )
    result = QAService._apply_work_product_verification(
        answer,
        {
            "overall": "partial",
            "unsupportedItemIds": [],
            "partialItemIds": ["E-1"],
            "reclassifications": [
                {"itemId": "E-1", "newBasis": "engineering_recommendation", "reason": "法规未规定字段"}
            ],
            "missingDeliverables": ["字段示例"],
        },
    )
    assert result.executiveAnswer[0].basis == "engineering_recommendation"
    assert len(result.qualityNotes) == 3
    assert result.unresolvedQuestions == []


def test_verifier_can_correct_factual_wording_in_place():
    a_source = source()
    answer = WorkProductReport(
        status="answered",
        answerTitle="标准年份",
        executiveAnswer=[
            ReportStatementV4(
                statementId="E-1",
                text="ISO/IEC 15459-2:2014适用。",
                basis="regulatory_requirement",
                sourceRefs=[a_source.sourceId],
            )
        ],
        sources=[a_source],
    )
    result = QAService._apply_work_product_verification(
        answer,
        {
            "overall": "partial",
            "unsupportedItemIds": [],
            "partialItemIds": [],
            "reclassifications": [],
            "revisions": [
                {"itemId": "E-1", "revisedText": "ISO/IEC 15459-2:2015适用。", "reason": "更正年份"}
            ],
            "missingDeliverables": [],
        },
    )
    assert result.executiveAnswer[0].text == "ISO/IEC 15459-2:2015适用。"
    assert "纠正文案" in result.qualityNotes[0].message


def test_encoding_question_profile_requests_field_example_and_checklist():
    profile = QAService._default_question_profile("他的编码规则和字段应该如何设计")
    assert profile["complexity"] == "complex"
    assert {"field_matrix", "example", "checklist"}.issubset(profile["requiredBlockTypes"])
