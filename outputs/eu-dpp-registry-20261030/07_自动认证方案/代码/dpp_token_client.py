"""DPP client-credentials example. One instance per customer/account/environment.

LocalEcSigner is for an isolated software-key integration test. Production can
implement JwtSigner using an authorized HSM/KMS; do not export its private key.
No real keys, client identifiers, assertions or access tokens are included.
"""
from __future__ import annotations

import random
import threading
import time
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Protocol

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


@dataclass(frozen=True)
class AccountConfig:
    tenant_id: str
    environment: str  # ACC or PROD
    client_id: str
    kid: str
    key_ref: str
    algorithm: str = "ES256"
    scope: str | None = None  # Only a scope already granted to this account.

    def __post_init__(self):
        if self.environment not in {"ACC", "PROD"}:
            raise ValueError("environment must be ACC or PROD")
        if self.algorithm not in {"ES256", "ES384", "ES512"}:
            raise ValueError("unsupported signing algorithm")
        if not all((self.tenant_id, self.client_id, self.kid, self.key_ref)):
            raise ValueError("tenant/account/credential configuration is required")

    @property
    def issuer(self) -> str:
        prefix = "auth.acc" if self.environment == "ACC" else "auth"
        return f"https://{prefix}.product-passport.ec.europa.eu"

    @property
    def token_endpoint(self) -> str:
        return self.issuer + "/oauth2/token"

    @property
    def resource(self) -> str:
        return self.issuer + "/resources/dpp/registry"

    @property
    def cache_key(self) -> tuple:
        return (self.tenant_id, self.environment, self.client_id, self.resource,
                self.scope or "", self.kid, self.key_ref, self.algorithm)


class JwtSigner(Protocol):
    def sign(self, claims: dict, headers: dict, algorithm: str, key_ref: str) -> str:
        """Return a complete compact JWS JWT using an authorized key reference."""


class LocalEcSigner:
    """Software-key demonstration; private_key comes from a protected local store."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey):
        self._private_key = private_key

    @classmethod
    def from_encrypted_pem(cls, pem: bytes, passphrase: bytes):
        return cls(serialization.load_pem_private_key(pem, password=passphrase))

    def sign(self, claims, headers, algorithm, key_ref):
        curves = {"ES256": ec.SECP256R1, "ES384": ec.SECP384R1, "ES512": ec.SECP521R1}
        if not isinstance(self._private_key.curve, curves[algorithm]):
            raise ValueError("key curve must match the registered algorithm")
        return jwt.encode(claims, self._private_key, algorithm=algorithm, headers=headers)


class TokenError(RuntimeError):
    """A deliberately redacted error; never attach a token response or JWT."""


class DppTokenClient:
    def __init__(self, config: AccountConfig, signer: JwtSigner, *, session=None,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep,
                 max_attempts: int = 3):
        self.config, self.signer = config, signer
        self.session = session if session is not None else requests.Session()
        self.clock, self.monotonic, self.sleeper = clock, monotonic, sleeper
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._token = None
        self._usable_until = 0.0

    def create_client_assertion(self) -> str:
        now = int(self.clock())
        claims = {"iss": self.config.client_id, "sub": self.config.client_id,
                  "aud": self.config.issuer, "iat": now, "exp": now + 120,
                  "jti": str(uuid.uuid4())}
        headers = {"typ": "client-authentication+jwt", "kid": self.config.kid}
        return self.signer.sign(claims, headers, self.config.algorithm, self.config.key_ref)

    def get_access_token(self) -> str:
        # Thread-level single-flight. Multi-process deployments need a separate
        # distributed cache/lock with the same customer/environment isolation.
        with self._lock:
            if self._token and self.monotonic() < self._usable_until:
                return self._token
            self._token = None
            for attempt in range(self.max_attempts):
                # New iat/exp/jti and a fresh signed assertion for EVERY attempt.
                form = {"grant_type": "client_credentials",
                        "client_id": self.config.client_id,
                        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                        "client_assertion": self.create_client_assertion(),
                        "resource": self.config.resource}
                if self.config.scope:
                    form["scope"] = self.config.scope
                started = self.monotonic()
                try:
                    response = self.session.post(
                        self.config.token_endpoint, data=form,
                        headers={"Accept": "application/json"},
                        timeout=(5, 20), allow_redirects=False)
                except (requests.Timeout, requests.ConnectionError):
                    if attempt + 1 == self.max_attempts:
                        raise TokenError("token request network failure") from None
                    self.sleeper(random.uniform(0, min(2 ** attempt, 8)))
                    continue

                if response.status_code in {429, 503}:
                    if attempt + 1 == self.max_attempts:
                        raise TokenError(f"temporary token endpoint HTTP {response.status_code}")
                    retry_after = self._retry_after(response.headers.get("Retry-After"))
                    self.sleeper(max(retry_after, random.uniform(0, min(2 ** attempt, 8))))
                    continue
                if response.status_code != 200:
                    # Do not forward server bodies, error_description or token material to logs.
                    raise TokenError(f"token request rejected: HTTP {response.status_code}")
                try:
                    data = response.json()
                    token, ttl = data["access_token"], data["expires_in"]
                    valid = (isinstance(token, str) and bool(token)
                             and str(data["token_type"]).lower() == "bearer"
                             and isinstance(ttl, int) and not isinstance(ttl, bool)
                             and 0 < ttl <= 900)
                except (ValueError, KeyError, TypeError):
                    valid = False
                if not valid:
                    raise TokenError("unexpected token response structure or lifetime")
                # Safety window is an implementation choice, not a protocol value.
                margin = min(60.0, ttl * 0.1)
                usable_until = started + ttl - margin
                if self.monotonic() >= usable_until:
                    raise TokenError("token lifetime exhausted during request")
                self._token, self._usable_until = token, usable_until
                return token
            raise TokenError("token acquisition failed")

    def invalidate(self, rejected_token: str) -> None:
        """A stale 401 must not invalidate a newer token obtained by another thread."""
        with self._lock:
            if self._token == rejected_token:
                self._token, self._usable_until = None, 0.0

    def _retry_after(self, value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return max(0.0, float(value))
        except (ValueError, TypeError):
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - self.clock())
            except (ValueError, TypeError, OverflowError):
                return 0.0
