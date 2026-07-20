from app.database import Database
from app.models import QuestionRequest
from app.qa_service import QAService


def completed_answer(summary: str) -> dict:
    return {
        "status": "answered",
        "summary": summary,
        "claims": [{"text": f"{summary}的论点"}],
        "implementationAdvice": [],
        "missingEvidence": [],
        "searchedSources": [],
    }


def test_conversation_history_is_isolated_and_excludes_current_job(tmp_path):
    database = Database(tmp_path / "memory.sqlite3")
    database.init()
    database.create_job("JOB-A1", "注册中心API有哪些", "CONV-A123", None)
    database.update_job("JOB-A1", status="completed", answer=completed_answer("A会话答案"))
    database.create_job("JOB-B1", "另一个用户的问题", "CONV-B123", None)
    database.update_job("JOB-B1", status="completed", answer=completed_answer("B会话答案"))
    database.create_job("JOB-A2", "那服务商呢", "CONV-A123", None)

    history = database.conversation_history("CONV-A123", exclude_job_id="JOB-A2", limit=5)

    assert [item["id"] for item in history] == ["JOB-A1"]
    assert history[0]["answer"]["summary"] == "A会话答案"


def test_limited_memory_provides_separate_context_queries():
    history = [
        {"question": "注册中心定义的API有哪些", "answer": completed_answer("注册API和语义API")},
        {"question": "接口有什么入参", "answer": completed_answer("法规只定义部分数据")},
    ]

    context_queries = QAService._retrieval_context_queries(history)
    memory = QAService._memory_payload(history)

    assert context_queries == ["注册中心定义的API有哪些", "接口有什么入参"]
    assert memory[-1]["answerSummary"] == "法规只定义部分数据"


def test_generation_prompt_forbids_using_memory_as_legal_evidence():
    history = [{"question": "上一个问题", "answer": completed_answer("历史结论")}]

    prompt = QAService._generation_prompt(
        QuestionRequest(question="那服务商呢", conversation_id="CONV-A123"), [], history
    )

    assert "<CONVERSATION_MEMORY>" in prompt
    assert "历史结论" in prompt
    assert "历史回答不是法规证据" in prompt
    assert "所有法律结论都必须由本轮<EVIDENCE>重新支持" in prompt
    assert "Battery Regulation的unique identifier" in prompt
