from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher

from app.auth import AuthService, DEFAULT_USERS
from app.database import Database


def test_default_accounts_are_seeded_idempotently_without_plaintext(tmp_path):
    database = Database(tmp_path / "auth.sqlite3")
    database.init()
    service = AuthService(database)
    service.seed_defaults()
    service.seed_defaults()

    expected = {
        "nmc": "admin",
        "hekai": "user",
        "kongruixia": "user",
        "sunbo": "user",
    }
    assert {item["username"]: item["role"] for item in DEFAULT_USERS} == expected
    for username, role in expected.items():
        user = database.get_user_by_username(username.upper())
        assert user is not None
        assert user["role"] == role
        assert user["password_hash"].startswith("$argon2id$")


def test_later_login_revokes_the_previous_session(tmp_path):
    database = Database(tmp_path / "auth.sqlite3")
    database.init()
    database.seed_users(
        [
            {
                "id": "USR-TEST",
                "username": "testuser",
                "role": "user",
                "password_hash": PasswordHasher().hash("unit-test-password"),
            }
        ]
    )
    service = AuthService(database)
    assert service.login("testuser", "wrong-password") is None

    first = service.login("TESTUSER", "unit-test-password")
    assert first is not None
    first_user, first_token, _ = first
    assert service.authenticate(first_token) == (first_user, "ok")

    second = service.login("testuser", "unit-test-password")
    assert second is not None
    second_user, second_token, _ = second
    assert service.authenticate(first_token) == (None, "session_replaced")
    assert service.authenticate(second_token) == (second_user, "ok")

    service.logout(second_token)
    assert service.authenticate(second_token) == (None, "logout")


def test_expired_session_and_answer_reference_permissions(tmp_path):
    database = Database(tmp_path / "auth.sqlite3")
    database.init()
    password_hash = PasswordHasher().hash("unit-test-password")
    database.seed_users(
        [{"id": "USR-A", "username": "usera", "role": "user", "password_hash": password_hash}]
    )
    service = AuthService(database)
    login = service.login("usera", "unit-test-password")
    assert login is not None
    _, token, _ = login
    with database.connect() as db:
        db.execute(
            "UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                service.token_hash(token),
            ),
        )
    assert service.authenticate(token) == (None, "session_expired")

    database.create_job("JOB-A", "问题", None, None, "USR-A")
    database.update_job(
        "JOB-A",
        status="completed",
        answer={
            "sources": [
                {"sourceId": "EVIDENCE-1", "documentId": "DOCUMENT-1"},
            ]
        },
    )
    assert database.job_belongs_to("JOB-A", "USR-A")
    assert database.user_answer_references("USR-A", "sourceId", "EVIDENCE-1")
    assert database.user_answer_references("USR-A", "documentId", "DOCUMENT-1")
    assert not database.user_answer_references("USR-A", "sourceId", "EVIDENCE-2")
