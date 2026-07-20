import asyncio
from datetime import datetime, timezone

from app.codex_runner import CodexResult
from app.config import Settings
from app.database import Database
from app.models import Evidence, QuestionRequest
from app.ingestion import ingest_document
from app.models import DocumentCreate
from app.retrieval import Retriever
from app.qa_service import QAService


class RecordingRetriever:
    def __init__(self):
        self.queries = []

    def search(self, query, **_):
        self.queries.append(query)
        evidence_id = f"EV-{len(self.queries):03d}"
        return [
            Evidence(
                evidenceId=evidence_id,
                documentId="DOC-001",
                documentTitle="Regulation (EU) 2024/1781",
                filename="regulation.pdf",
                article="Article 13",
                page=20,
                quote="The Commission shall set up a digital registry storing unique identifiers.",
            )
        ]


class DuplicateEvidenceRetriever(RecordingRetriever):
    def search(self, query, **_):
        self.queries.append(query)
        return [
            Evidence(
                evidenceId="EV-001",
                documentId="DOC-001",
                documentTitle="Regulation (EU) 2024/1781",
                filename="regulation.pdf",
                article="Article 13",
                page=20,
                quote="The Commission shall set up a digital registry storing unique identifiers.",
            )
        ]


class ExpandingRunner:
    def __init__(self, settings):
        self.settings = settings
        self.assessments = 0
        self.schemas = []

    async def run(self, _, schema_name, **__):
        self.schemas.append(schema_name)
        if schema_name == "research_assessment.schema.json":
            self.assessments += 1
            if self.assessments == 1:
                return CodexResult(
                    {
                        "decision": "search_local",
                        "reason": "需要按法规编号继续检索",
                        "coveredTopics": ["注册中心"],
                        "gaps": ["登记数据"],
                        "criticalGaps": [],
                        "nextQueries": ["Article 13 digital product passport registry"],
                        "webQueries": [],
                        "webRequired": False,
                    },
                    [],
                    "",
                )
            return CodexResult(
                {
                    "decision": "sufficient",
                    "reason": "正式法规已经覆盖问题",
                    "coveredTopics": ["注册中心", "登记数据"],
                    "gaps": [],
                    "criticalGaps": [],
                    "nextQueries": [],
                    "webQueries": [],
                    "webRequired": False,
                },
                [],
                "",
            )
        if schema_name == "work_product_report.schema.json":
            return CodexResult(
                {
                    "status": "answered",
                    "answerTitle": "注册中心保存的数据",
                    "executiveAnswer": [
                        {
                            "statementId": "P-01",
                            "text": "欧盟委员会应建立保存唯一标识符的注册中心。",
                            "basis": "regulatory_requirement",
                            "sourceRefs": ["EV-001"],
                        }
                    ],
                    "keyTakeaways": [],
                    "contentBlocks": [],
                    "serviceProviderActions": [],
                    "unresolvedQuestions": [],
                },
                [],
                "",
            )
        raise AssertionError(schema_name)


def test_research_flow_expands_local_search_and_skips_web_when_sufficient(tmp_path):
    database = Database(tmp_path / "flow.sqlite3")
    database.init()
    database.create_job("JOB-FLOW", "注册中心如何保存数据？", "CONV-FLOW", None)
    settings = Settings(
        law_retrieval_mode="legacy",
        data_dir=tmp_path,
        database_path=tmp_path / "flow.sqlite3",
        research_local_rounds=3,
        research_web_rounds=1,
        research_max_evidence=60,
        web_search_enabled=True,
        codex_verify_enabled=False,
        report_expansion_enabled=False,
    )
    retriever = RecordingRetriever()
    runner = ExpandingRunner(settings)
    service = QAService(database, retriever, runner, 5)

    asyncio.run(
        service._process(
            "JOB-FLOW",
            QuestionRequest(question="注册中心如何保存数据？", conversation_id="CONV-FLOW"),
        )
    )

    job = database.get_job("JOB-FLOW")
    assert job["status"] == "completed"
    assert job["answer"]["schemaVersion"] == "4.0"
    assert job["answer"]["status"] == "answered"
    assert retriever.queries == ["注册中心如何保存数据？", "Article 13 digital product passport registry"]
    assert "web_research.schema.json" not in runner.schemas
    assert [item["type"] for item in job["answer"]["researchTrace"]] == ["local", "local"]


class SimpleGapRunner(ExpandingRunner):
    async def run(self, _, schema_name, **__):
        self.schemas.append(schema_name)
        if schema_name == "research_assessment.schema.json":
            self.assessments += 1
            return CodexResult(
                {
                    "decision": "search_local",
                    "reason": "仍可补充背景资料",
                    "coveredTopics": ["定义"],
                    "gaps": ["非必要背景"],
                    "criticalGaps": [],
                    "nextQueries": ["补充背景资料"],
                    "webQueries": [],
                    "webRequired": False,
                    "questionProfile": {
                        "interpretedQuestion": "注册中心定义",
                        "complexity": "simple",
                        "intents": ["legal_explanation"],
                        "suggestedBlockType": "none",
                        "focusAreas": [],
                    },
                },
                [],
                "",
            )
        return await super().run(_, schema_name, **__)


def test_simple_question_stops_after_one_local_round(tmp_path):
    database = Database(tmp_path / "simple.sqlite3")
    database.init()
    database.create_job("JOB-SIMPLE", "注册中心定义", "CONV-SIMPLE", None)
    settings = Settings(
        law_retrieval_mode="legacy",
        data_dir=tmp_path,
        database_path=tmp_path / "simple.sqlite3",
        research_local_rounds=3,
        research_web_rounds=1,
        web_search_enabled=True,
        codex_verify_enabled=False,
        report_expansion_enabled=False,
    )
    retriever = RecordingRetriever()
    runner = SimpleGapRunner(settings)
    service = QAService(database, retriever, runner, 5)

    asyncio.run(
        service._process(
            "JOB-SIMPLE",
            QuestionRequest(question="注册中心定义", conversation_id="CONV-SIMPLE"),
        )
    )

    job = database.get_job("JOB-SIMPLE")
    assert job["status"] == "completed"
    assert runner.assessments == 1
    assert retriever.queries == ["注册中心定义"]
    assert [item["type"] for item in job["answer"]["researchTrace"]] == ["local"]
    assert "web_research.schema.json" not in runner.schemas


def test_follow_up_round_stops_without_another_model_call_when_no_new_evidence(tmp_path):
    database = Database(tmp_path / "no-gain.sqlite3")
    database.init()
    database.create_job("JOB-NO-GAIN", "注册中心如何保存数据？", "CONV-NO-GAIN", None)
    settings = Settings(
        law_retrieval_mode="legacy",
        data_dir=tmp_path,
        database_path=tmp_path / "no-gain.sqlite3",
        research_local_rounds=3,
        research_web_rounds=1,
        web_search_enabled=True,
        codex_verify_enabled=False,
        report_expansion_enabled=False,
    )
    retriever = DuplicateEvidenceRetriever()
    runner = ExpandingRunner(settings)
    service = QAService(database, retriever, runner, 5)

    asyncio.run(
        service._process(
            "JOB-NO-GAIN",
            QuestionRequest(question="注册中心如何保存数据？", conversation_id="CONV-NO-GAIN"),
        )
    )

    job = database.get_job("JOB-NO-GAIN")
    assert job["status"] == "completed"
    assert runner.assessments == 1
    assert len(retriever.queries) == 2
    assert job["answer"]["researchTrace"][1]["resultCount"] == 0
    assert "没有新增有效证据" in job["answer"]["researchTrace"][1]["decision"]


def test_web_search_requires_explicit_request_or_critical_gap():
    ordinary_gap = {
        "decision": "unresolved",
        "gaps": ["可以补充示例"],
        "criticalGaps": [],
        "webRequired": False,
    }
    critical_gap = {
        "decision": "unresolved",
        "gaps": ["缺少适用条款"],
        "criticalGaps": ["缺少适用条款"],
        "webRequired": False,
    }

    assert not QAService._should_search_web(
        ordinary_gap, explicit_request=False, local_search_exhausted=True
    )
    assert not QAService._should_search_web(
        critical_gap, explicit_request=False, local_search_exhausted=False
    )
    assert QAService._should_search_web(
        critical_gap, explicit_request=False, local_search_exhausted=True
    )
    assert QAService._should_search_web(
        ordinary_gap, explicit_request=True, local_search_exhausted=False
    )


class ToolAgentRunner:
    def __init__(self, settings, evidence_id):
        self.settings = settings
        self.evidence_id = evidence_id
        self.schemas = []

    async def run(self, _, schema_name, **kwargs):
        self.schemas.append(schema_name)
        assert schema_name == "local_law_agent.schema.json"
        assert kwargs.get("law_tools") is not None
        return CodexResult(
            {
                "decision": "sufficient",
                "reason": "已读取正式法规原文",
                "criticalGaps": [],
                "webQueries": [],
                "questionProfile": {
                    "interpretedQuestion": "海关核验流程",
                    "complexity": "simple",
                    "intents": ["process"],
                    "suggestedBlockType": "process",
                    "focusAreas": ["海关入参"],
                },
                "report": {
                    "status": "answered",
                    "answerTitle": "海关核验",
                    "executiveAnswer": [
                        {
                            "statementId": "S-1",
                            "text": "海关核验唯一登记标识符和商品编码。",
                            "basis": "regulatory_requirement",
                            "sourceRefs": [self.evidence_id],
                        }
                    ],
                    "keyTakeaways": [],
                    "contentBlocks": [],
                    "serviceProviderActions": [],
                    "unresolvedQuestions": [],
                },
            },
            [],
            "",
            law_trace=[
                {
                    "action": "search",
                    "query": "customs registration identifier commodity code",
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


def test_mcp_mode_generates_report_in_one_local_agent_call(tmp_path):
    source = tmp_path / "customs.txt"
    source.write_text(
        "Article 15\nCustoms authorities shall verify the unique registration identifier and commodity code.",
        encoding="utf-8",
    )
    database = Database(tmp_path / "mcp-flow.sqlite3")
    database.init()
    document = ingest_document(database, DocumentCreate(path=str(source)), tmp_path)
    evidence_id = database.searchable_passages()[0]["id"]
    database.create_job("JOB-MCP", "海关如何核验？", "CONV-MCP", None)
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "mcp-flow.sqlite3",
        law_retrieval_mode="mcp",
        web_search_enabled=False,
        codex_verify_enabled=False,
        report_expansion_enabled=False,
    )
    runner = ToolAgentRunner(settings, evidence_id)
    service = QAService(database, Retriever(database), runner, 5)

    asyncio.run(
        service._process(
            "JOB-MCP", QuestionRequest(question="海关如何核验？", conversation_id="CONV-MCP")
        )
    )

    job = database.get_job("JOB-MCP")
    assert document["passage_count"] == 1
    assert runner.schemas == ["local_law_agent.schema.json"]
    assert job["status"] == "completed"
    assert job["answer"]["sources"][0]["sourceId"] == evidence_id
    assert [item["action"] for item in job["answer"]["researchTrace"]] == ["search", "read"]
    assert job["answer"]["researchTrace"][0]["modelThinkingMs"] == 10.0
    assert job["answer"]["researchTrace"][1]["modelThinkingMs"] == 30.0
    assert job["answer"]["researchTrace"][1]["finalAnswerMs"] == 48.0
