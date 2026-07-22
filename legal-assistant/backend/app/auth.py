from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .database import Database


SESSION_COOKIE = "legal_assistant_session"
SESSION_TTL = timedelta(hours=24)

DEFAULT_USERS = [
    {
        "id": "USR-NMC",
        "username": "nmc",
        "role": "admin",
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$ECJqB75M/xR0rlNL2D0xag$XwMtf+XRiQ1SKVgKX2l5Cx7BTZqsjeEELRvlBzhiIWU",
    },
    {
        "id": "USR-HEKAI",
        "username": "hekai",
        "role": "user",
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$+SwQTQDaiykGhPVICwiXHA$gZ51KbZrs21GErQa0HzqimD2hQuuxnTEd6czq9QIF/k",
    },
    {
        "id": "USR-KONGRUIXIA",
        "username": "kongruixia",
        "role": "user",
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$jUyNLR9+9t/sXIQTqI/oiQ$TIV9R6fwVD57wUhgZx26ph0XtVhzB6j2PIxft+yinvU",
    },
    {
        "id": "USR-SUNBO",
        "username": "sunbo",
        "role": "user",
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$noblMGtQzbpSkmJGNWa3Og$CL/3f5FADyEUgOnvNIv0EPY92dYtSbOkTV5KhaUISUA",
    },
]


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: Literal["admin", "user"]
    session_id: str = ""

    @property
    def permissions(self) -> list[str]:
        if self.role == "admin":
            return ["ask", "library", "system", "monitor"]
        return ["ask"]


class AuthService:
    def __init__(self, database: Database):
        self.database = database
        self.password_hasher = PasswordHasher()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def seed_defaults(self) -> None:
        self.database.seed_users(DEFAULT_USERS)

    @staticmethod
    def describe_user_agent(user_agent: str | None) -> tuple[str, str, str]:
        value = user_agent or ""
        if "Edg/" in value:
            browser = "Microsoft Edge"
        elif "Chrome/" in value and "Chromium/" not in value:
            browser = "Google Chrome"
        elif "Firefox/" in value:
            browser = "Mozilla Firefox"
        elif "Safari/" in value and "Chrome/" not in value:
            browser = "Safari"
        elif value:
            browser = "其他浏览器"
        else:
            browser = "未记录"

        if "Windows NT" in value:
            operating_system = "Windows"
        elif "Android" in value:
            operating_system = "Android"
        elif re.search(r"iPhone|iPad|iPod", value):
            operating_system = "iOS/iPadOS"
        elif "Mac OS X" in value:
            operating_system = "macOS"
        elif "Linux" in value:
            operating_system = "Linux"
        else:
            operating_system = "未记录" if not value else "其他系统"

        if re.search(r"Mobile|Android|iPhone|iPod", value):
            device = "移动设备"
        elif "iPad" in value or "Tablet" in value:
            device = "平板设备"
        else:
            device = "桌面设备" if value else "未记录"
        return browser, operating_system, device

    def login(
        self, username: str, password: str, *, login_ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Principal, str, datetime] | None:
        normalized = username.strip().lower()
        user = self.database.get_user_by_username(normalized)
        if not user or not user["enabled"]:
            return None
        try:
            self.password_hasher.verify(user["password_hash"], password)
        except (VerifyMismatchError, InvalidHashError):
            return None
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + SESSION_TTL
        session_id = f"SES-{uuid.uuid4().hex.upper()}"
        browser, operating_system, device = self.describe_user_agent(user_agent)
        self.database.create_single_session(
            session_id=session_id,
            user_id=user["id"],
            token_hash=self.token_hash(token),
            expires_at=expires_at.isoformat(),
            login_ip=login_ip,
            user_agent=user_agent,
            browser=browser,
            operating_system=operating_system,
            device=device,
        )
        return Principal(user["id"], user["username"], user["role"], session_id), token, expires_at

    def authenticate(self, token: str | None) -> tuple[Principal | None, str]:
        if not token:
            return None, "authentication_required"
        session = self.database.resolve_session(self.token_hash(token))
        if not session:
            return None, "session_invalid"
        if session["revoked_at"]:
            return None, session["revoke_reason"] or "session_invalid"
        if not session["enabled"]:
            return None, "account_disabled"
        try:
            expires_at = datetime.fromisoformat(session["expires_at"])
        except (TypeError, ValueError):
            return None, "session_invalid"
        if expires_at <= datetime.now(timezone.utc):
            self.database.revoke_session(self.token_hash(token), "session_expired")
            return None, "session_expired"
        self.database.touch_session(session["session_id"], session.get("last_seen_at"))
        return Principal(
            session["user_id"], session["username"], session["role"], session["session_id"]
        ), "ok"

    def logout(self, token: str | None) -> None:
        if token:
            self.database.revoke_session(self.token_hash(token))
