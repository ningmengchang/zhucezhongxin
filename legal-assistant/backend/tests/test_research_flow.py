import asyncio
from datetime import datetime, timezone

from app.codex_runner import CodexResult
from app.config import Settings
from app.database import Database
from app.ingestion import ingest_document
from app.models import DocumentCreate, QuestionRequest, ReportSource
from app.qa_service import QAService
from app.retrieval import Retriever


class AutonomousRunner:
    def __init__(self, settings, evidence_id):
        self.settings = settings
        self.evidence_id = evidence_id
        self.calls = []

    async def run(self, prompt, schema_name, **kwargs):
        self.calls.append((prompt, schema_name, kwargs))
        return CodexResult(
            {
                "status": "answered",
                "markdown": "海关可根据具体制度和可用信息进行核验。",
            },
            [
                {
                    "type": "item.completed",
                    "item": {
                        "id": "WEB-TOOL-1",
                        "type": "web_search_call",
                        "query": "EU customs battery registry",
                        "results": [{"url": "https://example.com"}],
                    },
                }
            ],
            "",
            law_trace=[
                {
                    "action": "search",
                    "query": "customs battery registry",
                    "resultIds": [self.evidence_id],
                    "resultCount": 1,
                    "startedAt": "2026-07-20T00:00:00.010000+00:00",
                    "finishedAt": "2026-07-20T00:00:00.020000+00:00",
                    "durationMs": 10.0,
                },
                {
                    "action": "read",
                    "requestedId": self.evidence_id,
                    "resultIds": [self.evidence_id],
                    "resultCount": 1,
                    "startedAt": "2026-07-20T00:00:00.050000+00:00",
                    "finishedAt": "2026-07-20T00:00:00.052000+00:00",
                    "durationMs": 2.0,
                },
            ],
            started_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 20, 0, 0, 0, 100000, tzinfo=timezone.utc),
            duration_ms=100.0,
        )


def test_question_uses_one_autonomous_agent_with_all_tools(tmp_path):
    source = tmp_path / "customs.txt"
    source.write_text(
        "Article 15\nCustoms authorities shall verify the unique registration identifier.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "flow.sqlite3")
    database.init()
    ingest_document(database, DocumentCreate(path=str(source)), tmp_path)
    evidence_id = database.searchable_passages()[0]["id"]
    database.create_job("JOB-PREV", "注册中心定义了哪些接口", "CONV-AUTO", None)
    database.update_job(
        "JOB-PREV",
        status="completed",
        answer={
            "schemaVersion": "7.0",
            "status": "answered",
            "markdown": "旧答案：只以正式法规能够确认的接口为准。",
        },
    )
    database.create_job("JOB-AUTO", "海关如何核验？", "CONV-AUTO", None)
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "flow.sqlite3")
    runner = AutonomousRunner(settings, evidence_id)
    service = QAService(database, Retriever(database), runner, 5)

    asyncio.run(
        service._process(
            "JOB-AUTO", QuestionRequest(question="海关如何核验？", conversation_id="CONV-AUTO")
        )
    )

    job = database.get_job("JOB-AUTO")
    assert job["status"] == "completed"
    assert len(runner.calls) == 1
    prompt, schema_name, kwargs = runner.calls[0]
    assert schema_name == "markdown_answer.schema.json"
    assert kwargs["allow_web_search"] is True
    assert kwargs["law_tools"] is not None
    assert "均由你自行判断" in prompt
    assert "法规、用户指南、标准、草案、工作文件和说明材料" in prompt
    assert "不得默认把用户问题改写成“正式法规能够确认什么”" in prompt
    assert "旧答案：只以正式法规能够确认的接口为准" not in prompt
    assert "会话上下文" not in prompt
    assert "不得叙述模型、工具名称、搜索轮次" in prompt
    assert "自行识别原文中的标准法规术语" in prompt
    assert "SearchLaw不会替你翻译或扩展关键词" in prompt
    assert job["answer"]["status"] == "answered"
    assert job["answer"]["sources"][0]["authorityKind"] == "local_supplement"
    assert [item["type"] for item in job["answer"]["researchTrace"]] == [
        "local",
        "local",
        "web",
    ]
    assert job["answer"]["researchTrace"][2]["queries"] == ["EU customs battery registry"]


class NoToolRunner:
    def __init__(self, settings):
        self.settings = settings

    async def run(self, *_args, **_kwargs):
        return CodexResult(
            {"status": "answered", "markdown": "模型选择不调用工具并直接回答。"},
            [],
            "",
        )


def test_answer_is_blocked_when_model_uses_no_evidence(tmp_path):
    database = Database(tmp_path / "no-tools.sqlite3")
    database.init()
    database.create_job("JOB-NO-TOOLS", "普通问题", "CONV-NO-TOOLS", None)
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "no-tools.sqlite3")
    service = QAService(database, Retriever(database), NoToolRunner(settings), 5)

    asyncio.run(
        service._process(
            "JOB-NO-TOOLS", QuestionRequest(question="普通问题", conversation_id="CONV-NO-TOOLS")
        )
    )

    answer = database.get_job("JOB-NO-TOOLS")["answer"]
    assert answer["status"] == "unresolved"
    assert "暂时无法给出可靠结论" in answer["markdown"]
    assert answer["sources"] == []
    assert answer["researchTrace"] == []


class WebOnlyRunner:
    def __init__(self, settings):
        self.settings = settings

    async def run(self, *_args, **_kwargs):
        return CodexResult(
            {
                "status": "answered",
                "markdown": "该结论由可核验的外部原始来源支持。",
                "webSources": [
                    {
                        "title": "External primary source",
                        "url": "https://example.com/source",
                        "publisher": "Example authority",
                        "quote": "This is a sufficiently long verbatim quotation from the external source.",
                        "publicationDate": None,
                        "effectiveDate": None,
                    }
                ],
            },
            [],
            "",
        )


class AcceptWebEvidence:
    async def validate_many(self, _payloads):
        return (
            [
                ReportSource(
                    sourceId="WEB-TEST",
                    origin="web",
                    authorityKind="official_guidance",
                    title="External primary source",
                    quote="This is a sufficiently long verbatim quotation from the external source.",
                    url="https://example.com/source",
                )
            ],
            [],
        )


def test_verified_web_source_satisfies_evidence_gate(tmp_path):
    database = Database(tmp_path / "web-only.sqlite3")
    database.init()
    database.create_job("JOB-WEB", "外部来源问题", None, None)
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "web-only.sqlite3")
    service = QAService(database, Retriever(database), WebOnlyRunner(settings), 5)
    service.web_evidence = AcceptWebEvidence()

    asyncio.run(service._process("JOB-WEB", QuestionRequest(question="外部来源问题")))

    answer = database.get_job("JOB-WEB")["answer"]
    assert answer["status"] == "answered"
    assert answer["sources"][0]["sourceId"] == "WEB-TEST"
    assert "webSources" not in answer
