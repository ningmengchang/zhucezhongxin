from app.auth import Principal
from app.main import job_for_principal


def test_regular_user_job_hides_internal_research_details():
    job = {
        "id": "JOB-1",
        "question": "法规问题",
        "created_by": "USR-USER",
        "as_of_date": "2026-07-21",
        "status": "retrieving",
        "answer": {
            "markdown": "答复正文",
            "researchTrace": [{"type": "local", "queries": ["内部检索词"]}],
            "nested": {"qualityNotes": [{"message": "内部记录"}]},
            "sources": [{"title": "法规文件"}],
        },
        "error": "Codex internal error",
        "events": [
            {"stage": "retrieving", "message": "Codex正在调用SearchLaw", "at": "2026-07-21T00:00:00Z"}
        ],
        "created_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-21T00:00:01Z",
    }
    visible = job_for_principal(job, Principal("USR-USER", "user", "user"))

    assert visible["status"] == "processing"
    assert "researchTrace" not in visible["answer"]
    assert "qualityNotes" not in visible["answer"]["nested"]
    assert visible["answer"]["sources"] == [{"title": "法规文件"}]
    assert visible["error"] == "问题处理失败，请稍后重试"
    assert visible["events"][0]["message"] == "正在处理，请稍候"
    assert "created_by" not in visible and "as_of_date" not in visible


def test_admin_job_keeps_research_details_but_not_database_ownership_fields():
    job = {
        "id": "JOB-1",
        "question": "法规问题",
        "created_by": "USR-NMC",
        "status": "retrieving",
        "answer": {"markdown": "答复", "researchTrace": [{"type": "web"}]},
        "error": "detailed error",
        "events": [{"stage": "retrieving", "message": "详细事件", "at": "now"}],
        "created_at": "now",
        "updated_at": "now",
    }
    visible = job_for_principal(job, Principal("USR-NMC", "nmc", "admin"))

    assert visible["status"] == "retrieving"
    assert visible["answer"]["researchTrace"] == [{"type": "web"}]
    assert visible["events"][0]["message"] == "详细事件"
    assert visible["error"] == "detailed error"
    assert "created_by" not in visible
