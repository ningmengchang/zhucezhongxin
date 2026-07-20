from pathlib import Path

from app.codex_runner import CodexRunner
from app.config import Settings
from app.models import (
    ReportSource,
    ReportStatementV4,
    WorkProductBlock,
    WorkProductItem,
    WorkProductReport,
)
from app.qa_service import QAService


def source() -> ReportSource:
    return ReportSource(
        sourceId="EV-LOCAL",
        origin="local",
        authorityKind="legal_act",
        title="Test source",
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


def test_any_local_source_can_support_regulatory_requirement():
    local_source = source()
    local_source.authorityKind = "local_supplement"
    answer = WorkProductReport(
        status="answered",
        answerTitle="错误法规结论",
        executiveAnswer=[
            ReportStatementV4(
                statementId="E-1",
                text="这是法定要求。",
                basis="regulatory_requirement",
                sourceRefs=[local_source.sourceId],
            )
        ],
        sources=[local_source],
    )
    result = QAService._validate_work_product(answer)
    assert result.executiveAnswer
    assert result.status == "answered"
    assert result.qualityNotes == []
    assert result.unresolvedQuestions == []


def test_quality_gate_does_not_require_length_modules_or_provider_actions(tmp_path: Path):
    runner = CodexRunner(Settings(data_dir=tmp_path))
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
        contentBlocks=[
            WorkProductBlock(
                blockId="B-1",
                blockType="deep_analysis",
                title="详细分析",
                items=[
                    WorkProductItem(
                        itemId="I-1",
                        detail="法规只规定唯一性，具体字段格式需由实施方案确定。",
                        basis="engineering_recommendation",
                    )
                ],
            )
        ],
    )
    gaps = service._work_product_quality_gaps(
        answer,
        {
            "complexity": "complex",
            "intents": ["technical_design"],
            "suggestedBlockType": "field_matrix",
        },
    )
    assert gaps == []


def test_quality_gate_only_flags_missing_direct_answer_or_analysis(tmp_path: Path):
    runner = CodexRunner(Settings(data_dir=tmp_path))
    service = QAService(None, None, runner, 1)  # type: ignore[arg-type]
    answer = WorkProductReport(status="answered", answerTitle="未完成")
    gaps = service._work_product_quality_gaps(answer, {"complexity": "simple"})
    assert gaps == [
        "缺少直接回答用户问题的结论",
        "缺少对法规依据、适用条件和限制的详细分析",
    ]


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


def test_encoding_question_profile_suggests_only_one_useful_block():
    profile = QAService._default_question_profile("他的编码规则和字段应该如何设计")
    assert profile["complexity"] == "standard"
    assert profile["suggestedBlockType"] == "field_matrix"
    assert "targetCharacters" not in profile


def test_model_cannot_escalate_simple_question_complexity():
    profile = QAService._merge_question_profile(
        "注册中心定义",
        {
            "interpretedQuestion": "什么是注册中心",
            "complexity": "complex",
            "intents": ["legal_explanation"],
            "suggestedBlockType": "comparison_table",
            "focusAreas": ["定义"],
        },
    )
    assert profile["complexity"] == "simple"
    assert profile["suggestedBlockType"] == "comparison_table"
