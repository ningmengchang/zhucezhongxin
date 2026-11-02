"""Run with python -m unittest -v offline_checks. No external requests are made."""
import concurrent.futures
import time
import unittest
from dataclasses import replace

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from dpp_token_client import AccountConfig, DppTokenClient, LocalEcSigner, TokenError


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.data = data if data is not None else {
            "access_token": "LOCAL_TEST_TOKEN", "token_type": "Bearer", "expires_in": 900}

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class FakeSession:
    def __init__(self, responses=None, delay=0):
        self.responses = list(responses or [Response()])
        self.calls, self.delay = [], delay

    def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        if self.delay:
            time.sleep(self.delay)
        return self.responses.pop(0)


class TokenClientChecks(unittest.TestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.config = AccountConfig("LOCAL-TENANT", "ACC", "LOCAL-CLIENT", "LOCAL-KID", "LOCAL-KEY-REF")

    def client(self, session=None, **kwargs):
        return DppTokenClient(self.config, LocalEcSigner(self.key), session=session or FakeSession(), **kwargs)

    def decode(self, value, key=None, algorithm="ES256", config=None):
        cfg = config or self.config
        return jwt.decode(value, (key or self.key).public_key(), algorithms=[algorithm],
                          issuer=cfg.client_id, audience=cfg.issuer,
                          options={"require": ["iss", "sub", "aud", "iat", "exp", "jti"]})

    def test_new_jti_even_when_time_is_the_same(self):
        now = int(time.time())
        client = self.client(clock=lambda: now)
        a, b = client.create_client_assertion(), client.create_client_assertion()
        claims_a, claims_b = self.decode(a), self.decode(b)
        self.assertNotEqual(claims_a["jti"], claims_b["jti"])
        self.assertEqual(claims_a["iat"], claims_b["iat"])
        self.assertEqual(claims_a["exp"] - claims_a["iat"], 120)
        self.assertEqual(claims_a["sub"], self.config.client_id)
        self.assertEqual(jwt.get_unverified_header(a), {
            "alg": "ES256", "kid": "LOCAL-KID", "typ": "client-authentication+jwt"})

    def test_all_registered_curve_profiles(self):
        for algorithm, curve in [("ES256", ec.SECP256R1()), ("ES384", ec.SECP384R1()), ("ES512", ec.SECP521R1())]:
            with self.subTest(algorithm=algorithm):
                key = ec.generate_private_key(curve)
                cfg = replace(self.config, algorithm=algorithm)
                client = DppTokenClient(cfg, LocalEcSigner(key), session=FakeSession())
                self.decode(client.create_client_assertion(), key, algorithm, cfg)

    def test_wrong_audience_rejected_by_signature_validation(self):
        token = self.client().create_client_assertion()
        with self.assertRaises(jwt.InvalidAudienceError):
            jwt.decode(token, self.key.public_key(), algorithms=["ES256"], audience="https://wrong.invalid")

    def test_encrypted_pem_loaded_without_exporting_plaintext_file(self):
        pem = self.key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.BestAvailableEncryption(b"LOCAL-TEST-PASSPHRASE"))
        client = DppTokenClient(self.config, LocalEcSigner.from_encrypted_pem(pem, b"LOCAL-TEST-PASSPHRASE"), session=FakeSession())
        self.decode(client.create_client_assertion())

    def test_token_cache_and_early_renewal(self):
        current = [0.0]
        session = FakeSession([Response(), Response(data={"access_token": "LOCAL_TEST_TOKEN_2", "token_type": "Bearer", "expires_in": 900})])
        client = self.client(session, monotonic=lambda: current[0])
        self.assertEqual(client.get_access_token(), "LOCAL_TEST_TOKEN")
        self.assertEqual(client.get_access_token(), "LOCAL_TEST_TOKEN")
        self.assertEqual(len(session.calls), 1)
        current[0] = 841
        self.assertEqual(client.get_access_token(), "LOCAL_TEST_TOKEN_2")
        self.assertEqual(len(session.calls), 2)

    def test_token_form_and_no_redirect(self):
        session = FakeSession()
        self.client(session).get_access_token()
        endpoint, options = session.calls[0]
        self.assertEqual(endpoint, self.config.token_endpoint)
        self.assertFalse(options["allow_redirects"])
        form = options["data"]
        self.assertNotIn("scope", form)
        self.assertEqual(form["resource"], self.config.resource)
        self.assertEqual(form["grant_type"], "client_credentials")
        self.assertEqual(form["client_id"], self.config.client_id)
        self.assertNotIn("private_key", form)
        self.decode(form["client_assertion"])

    def test_retry_after_and_new_assertion_for_every_attempt(self):
        waits = []
        session = FakeSession([Response(429, headers={"Retry-After": "17"}), Response(503), Response()])
        self.client(session, sleeper=waits.append).get_access_token()
        claims = [self.decode(call[1]["data"]["client_assertion"]) for call in session.calls]
        self.assertEqual(len({x["jti"] for x in claims}), 3)
        self.assertGreaterEqual(waits[0], 17)

    def test_invalid_client_does_not_retry_or_echo_response(self):
        session = FakeSession([Response(400, {"error": "invalid_client", "error_description": "LOCAL_SECRET_MUST_NOT_APPEAR"})])
        with self.assertRaises(TokenError) as result:
            self.client(session).get_access_token()
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("LOCAL_SECRET_MUST_NOT_APPEAR", str(result.exception))

    def test_bad_token_responses_are_not_cached(self):
        for data in [ValueError("HTML"), {"access_token": "x", "token_type": "Bearer", "expires_in": 901}, {"access_token": "x", "token_type": "Bearer"}]:
            with self.subTest(data=type(data).__name__):
                client = self.client(FakeSession([Response(data=data)]))
                with self.assertRaises(TokenError):
                    client.get_access_token()
                self.assertIsNone(client._token)

    def test_parallel_calls_only_request_one_token(self):
        session = FakeSession(delay=0.02)
        client = self.client(session)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: client.get_access_token(), range(16)))
        self.assertEqual(set(results), {"LOCAL_TEST_TOKEN"})
        self.assertEqual(len(session.calls), 1)

    def test_customer_environment_and_credential_isolation(self):
        for change in [{"tenant_id": "OTHER-TENANT"}, {"environment": "PROD"}, {"kid": "NEW-KID"}, {"scope": "LOCAL_TEST_SCOPE"}]:
            self.assertNotEqual(self.config.cache_key, replace(self.config, **change).cache_key)
        prod = replace(self.config, environment="PROD")
        self.assertEqual(prod.issuer, "https://auth.product-passport.ec.europa.eu")
        self.assertNotEqual(prod.resource, self.config.resource)

    def test_old_401_does_not_clear_newer_token(self):
        client = self.client()
        client.get_access_token()
        client.invalidate("STALE_TOKEN")
        self.assertEqual(client._token, "LOCAL_TEST_TOKEN")
        client.invalidate("LOCAL_TEST_TOKEN")
        self.assertIsNone(client._token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
