from app.models import (
    ConciseLegalAnswer,
    DynamicAnswerTable,
    DynamicTableLegalAnswer,
    DynamicTableRow,
    ReportSource,
    ReportStatementV4,
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


def test_historical_v4_engineering_recommendation_without_source_is_preserved():
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


def test_concise_answer_keeps_supported_conclusion():
    a_source = source()
    answer = ConciseLegalAnswer(
        status="answered",
        title="登记规则",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="经济运营商应上传唯一标识符。",
            basis="regulatory_requirement",
            sourceRefs=[a_source.sourceId],
        ),
        sources=[a_source],
    )
    result = QAService._validate_concise_answer(answer, {"intents": ["legal_explanation"]})
    assert result.status == "answered"
    assert result.conclusion.statementId == "C-1"
    assert result.sources == [a_source]


def test_concise_answer_replaces_unsupported_legal_conclusion():
    answer = ConciseLegalAnswer(
        status="answered",
        title="无来源结论",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="这是法规直接要求。",
            basis="regulatory_requirement",
            sourceRefs=["MISSING"],
        ),
    )
    result = QAService._validate_concise_answer(answer)
    assert result.status == "unresolved"
    assert result.conclusion.statementId == "C-UNRESOLVED"
    assert result.conclusion.basis == "uncertainty"


def test_concise_answer_deduplicates_and_limits_explanations():
    a_source = source()
    answer = ConciseLegalAnswer(
        status="answered",
        title="简明答复",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="经济运营商应上传唯一标识符。",
            basis="regulatory_requirement",
            sourceRefs=[a_source.sourceId],
        ),
        explanations=[
            ReportStatementV4(
                statementId=f"E-{index}",
                text="经济运营商应上传唯一标识符。" if index == 0 else f"补充说明{index}",
                basis="regulatory_requirement",
                sourceRefs=[a_source.sourceId],
            )
            for index in range(5)
        ],
        sources=[a_source],
    )
    result = QAService._validate_concise_answer(answer)
    assert [item.text for item in result.explanations] == ["补充说明1", "补充说明2", "补充说明3"]
    assert result.status == "partial"


def test_provider_advice_only_kept_for_relevant_question_intent():
    answer = ConciseLegalAnswer(
        status="answered",
        title="字段设计",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="法规没有规定内部数据库字段名称。",
            basis="engineering_recommendation",
        ),
        providerAdvice=[
            ReportStatementV4(
                statementId="A-1",
                text="建议、非强制：分别保存产品标识和登记标识。",
                basis="engineering_recommendation",
            )
        ],
    )
    concept_result = QAService._validate_concise_answer(
        answer.model_copy(deep=True), {"intents": ["legal_explanation"]}
    )
    technical_result = QAService._validate_concise_answer(
        answer.model_copy(deep=True), {"intents": ["technical_design"]}
    )
    assert concept_result.providerAdvice == []
    assert len(technical_result.providerAdvice) == 1


def test_dynamic_table_keeps_supported_rows_and_normalizes_cells():
    a_source = source()
    answer = DynamicTableLegalAnswer(
        status="answered",
        title="登记入参",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="经济运营商应上传唯一标识符。",
            basis="regulatory_requirement",
            sourceRefs=[a_source.sourceId],
        ),
        table=DynamicAnswerTable(
            columns=["主体", "入参", "用途"],
            rows=[
                DynamicTableRow(
                    rowId="R-1",
                    cells=["经济运营商", "唯一标识符"],
                    basis="regulatory_requirement",
                    sourceRefs=[a_source.sourceId],
                )
            ],
        ),
        sources=[a_source],
    )
    result = QAService._validate_dynamic_table_answer(
        answer, {"intents": ["technical_design"]}
    )
    assert result.schemaVersion == "6.0"
    assert result.table.rows[0].cells == ["经济运营商", "唯一标识符", "—"]
    assert result.sources == [a_source]


def test_dynamic_table_removes_unsupported_regulatory_row():
    a_source = source()
    answer = DynamicTableLegalAnswer(
        status="answered",
        title="无依据行",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="经济运营商应上传唯一标识符。",
            basis="regulatory_requirement",
            sourceRefs=[a_source.sourceId],
        ),
        table=DynamicAnswerTable(
            columns=["事项", "要求"],
            rows=[
                DynamicTableRow(
                    rowId="R-1",
                    cells=["接口", "法规规定固定字段名"],
                    basis="regulatory_requirement",
                    sourceRefs=["MISSING"],
                )
            ],
        ),
        sources=[a_source],
    )
    result = QAService._validate_dynamic_table_answer(answer)
    assert result.status == "partial"
    assert result.table.rows[0].basis == "uncertainty"


def test_dynamic_table_only_allows_engineering_when_implementation_is_asked():
    answer = DynamicTableLegalAnswer(
        status="answered",
        title="字段设计",
        conclusion=ReportStatementV4(
            statementId="C-1",
            text="建议、非强制：分别保存两个标识符。",
            basis="engineering_recommendation",
        ),
        table=DynamicAnswerTable(
            columns=["字段", "设计"],
            rows=[
                DynamicTableRow(
                    rowId="R-1",
                    cells=["identifier", "建议、非强制：分字段保存"],
                    basis="engineering_recommendation",
                )
            ],
        ),
    )
    blocked = QAService._validate_dynamic_table_answer(
        answer.model_copy(deep=True), {"intents": ["technical_design"]}
    )
    allowed = QAService._validate_dynamic_table_answer(
        answer.model_copy(deep=True), {"intents": ["implementation"]}
    )
    assert blocked.status == "unresolved"
    assert allowed.status == "answered"
    assert allowed.table.rows[0].rowId == "R-1"


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
