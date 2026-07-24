from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

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


def test_self_registration_creates_enabled_regular_user_and_rejects_duplicate(tmp_path):
    database = Database(tmp_path / "auth.sqlite3")
    database.init()
    service = AuthService(database)
    service.seed_defaults()

    username = service.register("  新用户.Test-1  ", "register-password", "register-password")
    assert username == "新用户.test-1"
    user = database.get_user_by_username("新用户.TEST-1")
    assert user is not None
    assert user["role"] == "user"
    assert user["enabled"] == 1
    assert user["password_hash"].startswith("$argon2id$")
    assert service.login(username, "register-password") is not None

    try:
        service.register("NMC", "another-password", "another-password")
    except FileExistsError as exc:
        assert str(exc) == "该用户名已存在"
    else:
        raise AssertionError("nmc must not be registered twice")


def test_registration_validation_and_concurrent_name_uniqueness(tmp_path):
    database = Database(tmp_path / "auth.sqlite3")
    database.init()
    service = AuthService(database)

    for username, password, confirmation, message in (
        ("a", "valid-password", "valid-password", "用户名长度必须为2至32位"),
        ("-invalid", "valid-password", "valid-password", "用户名必须以中文、英文字母或数字开头和结尾"),
        ("valid user", "valid-password", "valid-password", "用户名只能包含中文、英文字母、数字、下划线、短横线和点"),
        ("valid-user", "short", "short", "密码长度必须为8至128位"),
        ("valid-user", "valid-password", "different-password", "两次输入的密码不一致"),
    ):
        try:
            service.register(username, password, confirmation)
        except ValueError as exc:
            assert str(exc) == message
        else:
            raise AssertionError(f"{username} should fail validation")

    def create_same_user(_):
        try:
            service.register("Concurrent.User", "valid-password", "valid-password")
            return "created"
        except FileExistsError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_same_user, range(2)))
    assert sorted(results) == ["created", "duplicate"]


def test_seed_keeps_existing_password_and_repairs_nmc_admin_role(tmp_path):
    database = Database(tmp_path / "auth.sqlite3")
    database.init()
    service = AuthService(database)
    service.seed_defaults()
    before = database.get_user_by_username("nmc")
    assert before is not None
    with database.connect() as db:
        db.execute("UPDATE users SET role='user', enabled=0 WHERE username='nmc'")

    service.seed_defaults()
    repaired = database.get_user_by_username("NMC")
    assert repaired is not None
    assert repaired["role"] == "admin"
    assert repaired["enabled"] == 1
    assert repaired["password_hash"] == before["password_hash"]
