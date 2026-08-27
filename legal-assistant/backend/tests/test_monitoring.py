from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher

from app.auth import AuthService
from app.database import Database


def make_database(tmp_path):
    database = Database(tmp_path / "monitor.sqlite3")
    database.init()
    database.seed_users(
        [
            {
                "id": "USR-ADMIN",
                "username": "admin",
                "role": "admin",
                "password_hash": PasswordHasher().hash("monitor-password"),
            },
            {
                "id": "USR-USER",
                "username": "user",
                "role": "user",
                "password_hash": PasswordHasher().hash("monitor-password"),
            },
        ]
    )
    return database


def test_login_metadata_online_state_and_admin_revoke(tmp_path):
    database = make_database(tmp_path)
    service = AuthService(database)
    result = service.login(
        "user",
        "monitor-password",
        login_ip="192.168.110.25",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    assert result is not None
    principal, token, _ = result

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    users = database.monitoring_users(online_cutoff=cutoff)
    user = next(item for item in users if item["username"] == "user")
    assert user["online"] is True
    assert user["login_ip"] == "192.168.110.25"
    assert user["browser"] == "Google Chrome"
    assert user["operating_system"] == "Windows"

    sessions = database.monitoring_sessions(
        page=1, page_size=20, online_cutoff=cutoff
    )
    assert sessions["total"] == 1
    assert sessions["items"][0]["status"] == "online"
    assert database.revoke_session_by_id(principal.session_id)
    assert service.authenticate(token) == (None, "admin_revoked")


def test_question_monitoring_details_filters_and_retention(tmp_path):
    database = make_database(tmp_path)
    database.create_job("JOB-NEW", "授权代表是什么", None, None, "USR-USER")
    database.update_job(
        "JOB-NEW",
        status="completed",
        answer={
            "schemaVersion": "7.0",
            "markdown": "完整回答",
            "sources": [{"sourceId": "SRC-1", "origin": "local"}],
            "researchTrace": [{"round": 1, "type": "local"}],
        },
        event={"stage": "completed", "message": "完成"},
    )
    listing = database.monitoring_questions(
        page=1, page_size=20, username="use", status="completed", keyword="授权代表"
    )
    assert listing["total"] == 1
    detail = database.monitoring_question_detail("JOB-NEW")
    assert detail is not None
    assert detail["username"] == "user"
    assert detail["answer"]["researchTrace"][0]["type"] == "local"
    assert detail["events"][0]["stage"] == "completed"

    database.create_job("JOB-OLD", "过期问题", None, None, "USR-USER")
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with database.connect() as db:
        db.execute("UPDATE jobs SET created_at=?, updated_at=? WHERE id='JOB-OLD'", (old, old))
        db.execute(
            """
            INSERT INTO auth_sessions
                (id,user_id,token_hash,expires_at,created_at,last_seen_at)
            VALUES ('SES-OLD','USR-USER','old-hash',?,?,?)
            """,
            (old, old, old),
        )
    removed = database.cleanup_monitoring_data(30)
    assert removed == {"sessions": 1, "jobs": 1}
    assert database.get_job("JOB-OLD") is None
    assert database.get_job("JOB-NEW") is not None
    assert database.get_user_by_username("user") is not None


def test_startup_marks_only_unfinished_jobs_as_failed(tmp_path):
    database = make_database(tmp_path)
    database.create_job("JOB-QUEUED", "排队中", None, None, "USR-USER")
    database.create_job("JOB-RUNNING", "处理中", None, None, "USR-USER")
    database.update_job("JOB-RUNNING", status="retrieving")
    database.create_job("JOB-DONE", "已完成", None, None, "USR-USER")
    database.update_job(
        "JOB-DONE",
        status="completed",
        answer={"schemaVersion": "7.0", "status": "answered", "markdown": "完成"},
    )

    assert database.fail_unfinished_jobs("服务已重启") == 2
    assert database.get_job("JOB-QUEUED")["status"] == "failed"
    assert database.get_job("JOB-RUNNING")["error"] == "服务已重启"
    assert database.get_job("JOB-RUNNING")["events"][-1]["stage"] == "failed"
    assert database.get_job("JOB-DONE")["status"] == "completed"
