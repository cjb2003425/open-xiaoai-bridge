import json
import asyncio
import base64
import hashlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ChatGPTAuthConfigTest(unittest.TestCase):
    def test_config_uses_fixed_official_endpoints_and_defaults(self):
        from core.chatgpt_auth import ChatGPTAuthConfig

        config = ChatGPTAuthConfig.from_mapping({})

        self.assertEqual("https://auth.openai.com", config.issuer)
        self.assertEqual(
            "https://auth.openai.com/api/accounts/authorize",
            config.authorization_endpoint,
        )
        self.assertEqual(
            "https://auth.openai.com/api/accounts/oauth/token",
            config.token_endpoint,
        )
        self.assertEqual("https://api.openai.com/v1", config.api_base_url)
        self.assertEqual("https://api.openai.com/v1", config.resource)
        self.assertEqual("127.0.0.1", config.callback_host)
        self.assertEqual(1455, config.callback_port)
        self.assertEqual("/auth/callback", config.callback_path)
        self.assertEqual("./data/chatgpt-siwc.json", config.credential_file)
        self.assertIn("chatgpt.tokens.use.direct", config.scopes)

    def test_config_rejects_non_loopback_callback(self):
        from core.chatgpt_auth import ChatGPTAuthConfig, ChatGPTAuthError

        for mapping in (
            {"callback_host": "0.0.0.0"},
            {"callback_host": "localhost"},
            {"callback_path": "/callback"},
            {"callback_port": 0},
            {"callback_port": 70000},
        ):
            with self.subTest(mapping=mapping):
                with self.assertRaises(ChatGPTAuthError):
                    ChatGPTAuthConfig.from_mapping(mapping)


class ChatGPTCredentialsTest(unittest.TestCase):
    def valid_mapping(self):
        return {
            "issued_client_id": "issued-client",
            "ext_agent_host_id": "host-opaque-id",
            "issuer": "https://auth.openai.com",
            "subject": "account-subject",
            "email": "person@example.com",
            "id_token": "id-secret",
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_type": "Bearer",
            "expires_at": 2_000_000_000.0,
            "scopes": [
                "openid",
                "profile",
                "email",
                "offline_access",
                "resource.invoke",
                "chatgpt.tokens.use.direct",
            ],
            "selected_model": "gpt-test",
        }

    def test_credentials_require_issued_client_tokens_scope_and_identity(self):
        from core.chatgpt_auth import ChatGPTAuthError, ChatGPTCredentials

        required = (
            "issued_client_id",
            "ext_agent_host_id",
            "issuer",
            "subject",
            "email",
            "id_token",
            "access_token",
            "refresh_token",
        )
        for field in required:
            with self.subTest(field=field):
                data = self.valid_mapping()
                data[field] = ""
                with self.assertRaises(ChatGPTAuthError):
                    ChatGPTCredentials.from_mapping(data)

        data = self.valid_mapping()
        data["scopes"].remove("chatgpt.tokens.use.direct")
        with self.assertRaises(ChatGPTAuthError):
            ChatGPTCredentials.from_mapping(data)

    def test_secret_fields_are_redacted_from_repr(self):
        from core.chatgpt_auth import ChatGPTCredentials

        credentials = ChatGPTCredentials.from_mapping(self.valid_mapping())
        rendered = repr(credentials)
        for secret in ("id-secret", "access-secret", "refresh-secret"):
            self.assertNotIn(secret, rendered)


class ChatGPTCredentialStoreTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "nested" / "credentials.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def credentials(self):
        from core.chatgpt_auth import ChatGPTCredentials

        return ChatGPTCredentials.from_mapping(ChatGPTCredentialsTest().valid_mapping())

    def test_store_writes_atomically_with_mode_0600(self):
        from core.chatgpt_auth import ChatGPTCredentialStore

        store = ChatGPTCredentialStore(str(self.path))
        expected = self.credentials()
        store.save(expected)

        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(expected, store.load())
        self.assertEqual([], list(self.path.parent.glob(f".{self.path.name}.*.tmp")))

    def test_corrupt_store_is_quarantined(self):
        from core.chatgpt_auth import ChatGPTCredentialStore

        self.path.parent.mkdir(parents=True)
        self.path.write_text("not-json", encoding="utf-8")
        store = ChatGPTCredentialStore(str(self.path))

        self.assertIsNone(store.load())
        self.assertFalse(self.path.exists())
        quarantined = list(self.path.parent.glob(f"{self.path.name}.invalid*"))
        self.assertEqual(1, len(quarantined))
        self.assertEqual("not-json", quarantined[0].read_text(encoding="utf-8"))

    def test_delete_removes_local_credentials(self):
        from core.chatgpt_auth import ChatGPTCredentialStore

        store = ChatGPTCredentialStore(str(self.path))
        store.save(self.credentials())
        self.assertTrue(store.delete())
        self.assertFalse(self.path.exists())
        self.assertFalse(store.delete())

    def test_default_credential_variants_are_gitignored(self):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("data/chatgpt-siwc.json*", rules)
        self.assertNotIn("data/openai-oauth-token.json*", rules)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, content_type=None):
        del content_type
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(*response)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(*response)

    async def close(self):
        self.closed = True


class ChatGPTLoginTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.chatgpt_auth import ChatGPTAuthConfig, ChatGPTAuthManager

        self.config = ChatGPTAuthConfig.from_mapping({})
        self.store = MagicMock()
        self.store.load.return_value = None
        self.manager = ChatGPTAuthManager(self.config, store=self.store)

    def query(self, url):
        return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}

    def test_first_login_uses_dynamic_client_pkce_and_exact_official_parameters(self):
        url = self.manager._build_authorization_url(
            state="state-value",
            nonce="nonce-value",
            code_challenge="challenge-value",
            ext_agent_host_id="host-value",
        )
        parsed = urlparse(url)
        query = self.query(url)

        self.assertEqual("https", parsed.scheme)
        self.assertEqual("auth.openai.com", parsed.netloc)
        self.assertEqual("/api/accounts/authorize", parsed.path)
        self.assertEqual("dynamic_agent_client", query["client_id"])
        self.assertEqual("code", query["response_type"])
        self.assertEqual(self.config.redirect_uri, query["redirect_uri"])
        self.assertEqual("S256", query["code_challenge_method"])
        self.assertEqual("challenge-value", query["code_challenge"])
        self.assertEqual("state-value", query["state"])
        self.assertEqual("nonce-value", query["nonce"])
        self.assertEqual(self.config.resource, query["resource"])
        self.assertEqual(self.config.agent_name, query["agent_name_hint"])
        self.assertEqual("host-value", query["ext_agent_host_id"])
        self.assertEqual(" ".join(self.config.scopes), query["scope"])

    def test_callback_binds_loopback_exact_path_and_validates_state(self):
        callback = self.manager._validate_callback_request(
            path="/auth/callback",
            query={"state": "expected", "code": "code", "client_id": "issued"},
            expected_state="expected",
        )
        self.assertEqual(("code", "issued"), callback)
        self.assertEqual("127.0.0.1", self.config.callback_host)

        for path, state in (("/wrong", "expected"), ("/auth/callback", "wrong")):
            with self.subTest(path=path, state=state):
                with self.assertRaisesRegex(Exception, "callback|state"):
                    self.manager._validate_callback_request(
                        path=path,
                        query={"state": state, "code": "code", "client_id": "issued"},
                        expected_state="expected",
                    )

    async def test_denial_timeout_wrong_path_and_wrong_state_preserve_old_credentials(self):
        old = ChatGPTCredentialsTest().valid_mapping()
        from core.chatgpt_auth import ChatGPTCredentials

        self.manager._credentials = ChatGPTCredentials.from_mapping(old)
        for failure in (
            RuntimeError("authorization denied"),
            asyncio.TimeoutError(),
            RuntimeError("invalid callback path"),
            RuntimeError("invalid OAuth state"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.manager._await_callback = AsyncMock(side_effect=failure)
                with self.assertRaises(Exception):
                    await self.manager.login(MagicMock(), MagicMock())
                self.store.save.assert_not_called()
                self.assertEqual("access-secret", self.manager._credentials.access_token)

    def test_reauthorization_reuses_client_and_redacts_id_token_hint_url(self):
        from core.chatgpt_auth import ChatGPTCredentials

        credentials = ChatGPTCredentials.from_mapping(ChatGPTCredentialsTest().valid_mapping())
        url = self.manager._build_authorization_url(
            state="state",
            nonce="nonce",
            code_challenge="challenge",
            ext_agent_host_id=credentials.ext_agent_host_id,
            credentials=credentials,
        )
        query = self.query(url)
        self.assertEqual("issued-client", query["client_id"])
        self.assertEqual("id-secret", query["id_token_hint"])
        self.assertNotIn("id-secret", self.manager._redact_authorization_url(url))

    def test_reauthorization_rejects_changed_callback_client_id(self):
        from core.chatgpt_auth import ChatGPTAuthError, ChatGPTCredentials

        self.manager._credentials = ChatGPTCredentials.from_mapping(
            ChatGPTCredentialsTest().valid_mapping()
        )
        with self.assertRaisesRegex(ChatGPTAuthError, "client"):
            self.manager._validate_callback_client_id("different-client")

    async def test_exchange_uses_issued_client_without_secret(self):
        session = FakeSession([(200, {"access_token": "a"})])
        self.manager._session = session
        body = await self.manager._exchange_code(
            code="auth-code",
            code_verifier="verifier",
            issued_client_id="issued-client",
        )
        self.assertEqual("a", body["access_token"])
        _, url, kwargs = session.calls[0]
        self.assertEqual(self.config.token_endpoint, url)
        self.assertEqual("issued-client", kwargs["data"]["client_id"])
        self.assertEqual(self.config.redirect_uri, kwargs["data"]["redirect_uri"])
        self.assertNotIn("client_secret", kwargs["data"])

    async def test_missing_direct_usage_scope_is_rejected(self):
        from core.chatgpt_auth import ChatGPTAuthError

        with self.assertRaisesRegex(ChatGPTAuthError, "scope"):
            self.manager._normalize_token_response(
                {
                    "id_token": "id",
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "scope": "openid profile email offline_access resource.invoke",
                }
            )


class ChatGPTIDTokenTest(unittest.TestCase):
    def setUp(self):
        from core.chatgpt_auth import ChatGPTAuthConfig, ChatGPTAuthManager

        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
        self.jwks = {"keys": [public_jwk]}
        self.manager = ChatGPTAuthManager(ChatGPTAuthConfig.from_mapping({}))

    def token(self, **overrides):
        claims = {
            "iss": "https://auth.openai.com",
            "aud": "issued-client",
            "sub": "account-subject",
            "email": "person@example.com",
            "nonce": "expected-nonce",
            "iat": 2_000_000_000,
            "exp": 2_000_003_600,
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def test_id_token_verifies_jwks_issuer_audience_expiry_and_nonce(self):
        claims = self.manager._validate_id_token(
            self.token(),
            self.jwks,
            issued_client_id="issued-client",
            expected_nonce="expected-nonce",
            current_time=2_000_000_100,
        )
        self.assertEqual("account-subject", claims["sub"])

    def test_invalid_id_token_fields_are_rejected(self):
        from core.chatgpt_auth import ChatGPTAuthError

        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        invalid_cases = {
            "signature": jwt.encode(
                {
                    "iss": "https://auth.openai.com",
                    "aud": "issued-client",
                    "sub": "s",
                    "email": "e@example.com",
                    "nonce": "expected-nonce",
                    "exp": 2_000_003_600,
                },
                other_key,
                algorithm="RS256",
                headers={"kid": "test-key"},
            ),
            "issuer": self.token(iss="https://evil.example"),
            "audience": self.token(aud="wrong-client"),
            "expiry": self.token(exp=2_000_000_000),
            "nonce": self.token(nonce="wrong-nonce"),
        }
        for name, token in invalid_cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ChatGPTAuthError):
                    self.manager._validate_id_token(
                        token,
                        self.jwks,
                        issued_client_id="issued-client",
                        expected_nonce="expected-nonce",
                        current_time=2_000_000_100,
                    )


class ChatGPTLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, *, expires_at=2_000.0, responses=()):
        from core.chatgpt_auth import (
            ChatGPTAuthConfig,
            ChatGPTAuthManager,
            ChatGPTCredentials,
        )

        data = ChatGPTCredentialsTest().valid_mapping()
        data["expires_at"] = expires_at
        credentials = ChatGPTCredentials.from_mapping(data)
        store = MagicMock()
        store.load.return_value = credentials
        session = FakeSession(responses)
        manager = ChatGPTAuthManager(
            ChatGPTAuthConfig.from_mapping({"refresh_leeway": 120}),
            store=store,
            session_factory=lambda: session,
            clock=lambda: 1_000.0,
        )
        return manager, store, session

    async def test_valid_access_token_skips_refresh(self):
        manager, store, session = self.make_manager(expires_at=2_000.0)
        self.assertEqual("access-secret", await manager.get_access_token())
        self.assertEqual([], session.calls)
        store.save.assert_not_called()

    async def test_near_expiry_refresh_rotates_and_persists_tokens(self):
        manager, store, session = self.make_manager(
            expires_at=1_100.0,
            responses=[
                (
                    200,
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "scope": "openid chatgpt.tokens.use.direct",
                    },
                )
            ],
        )
        self.assertEqual("new-access", await manager.get_access_token())
        saved = store.save.call_args.args[0]
        self.assertEqual("new-refresh", saved.refresh_token)
        self.assertEqual(4_600.0, saved.expires_at)
        _, _, kwargs = session.calls[0]
        self.assertEqual("refresh_token", kwargs["data"]["grant_type"])
        self.assertEqual("issued-client", kwargs["data"]["client_id"])
        self.assertEqual("refresh-secret", kwargs["data"]["refresh_token"])
        self.assertNotIn("scope", kwargs["data"])

    async def test_concurrent_first_call_after_restart_shares_one_refresh(self):
        manager, store, _session = self.make_manager(expires_at=1_100.0)
        started = asyncio.Event()
        release = asyncio.Event()

        async def refresh_once(credentials):
            started.set()
            await release.wait()
            return credentials.__class__.from_mapping(
                {**credentials.to_mapping(), "access_token": "shared", "expires_at": 4_600}
            )

        manager._refresh_credentials = AsyncMock(side_effect=refresh_once)
        first = asyncio.create_task(manager.get_access_token())
        await started.wait()
        second = asyncio.create_task(manager.get_access_token())
        await asyncio.sleep(0)
        release.set()
        self.assertEqual(["shared", "shared"], await asyncio.gather(first, second))
        manager._refresh_credentials.assert_awaited_once()
        store.save.assert_called_once()

    async def test_refresh_failure_preserves_still_valid_credentials(self):
        import aiohttp

        manager, store, _session = self.make_manager(
            expires_at=1_100.0,
            responses=[aiohttp.ClientConnectionError("offline")],
        )
        self.assertEqual("access-secret", await manager.get_access_token())
        self.assertEqual("access-secret", manager.credentials.access_token)
        store.save.assert_not_called()

    async def test_invalid_grant_requires_login_without_deleting_diagnostics(self):
        from core.chatgpt_auth import ChatGPTAuthError

        manager, store, _session = self.make_manager(
            expires_at=900.0,
            responses=[(400, {"error": "invalid_grant"})],
        )
        with self.assertRaisesRegex(ChatGPTAuthError, "sign in again"):
            await manager.get_access_token()
        self.assertEqual("refresh-secret", manager.credentials.refresh_token)
        store.delete.assert_not_called()

    async def test_model_catalog_keeps_visible_order(self):
        manager, _store, _session = self.make_manager(
            responses=[
                (
                    200,
                    {
                        "models": [
                            {"slug": "first", "display_name": "First", "visibility": "list"},
                            {"slug": "hidden", "visibility": "hidden"},
                            {"slug": "second", "display_name": "Second", "visibility": "list"},
                        ]
                    },
                )
            ]
        )
        models = await manager.list_models()
        self.assertEqual(["first", "second"], [model.slug for model in models])

    async def test_configured_or_selected_model_must_be_visible(self):
        from core.chatgpt_auth import ChatGPTAuthError, ChatGPTModel

        manager, _store, _session = self.make_manager()
        manager.list_models = AsyncMock(return_value=[ChatGPTModel("visible", "Visible")])
        self.assertEqual("visible", await manager.require_visible_model("visible"))
        with self.assertRaisesRegex(ChatGPTAuthError, "not available"):
            await manager.require_visible_model("missing")

    async def test_logout_revokes_then_deletes(self):
        manager, store, session = self.make_manager(
            responses=[
                (200, {"revocation_endpoint": "https://auth.openai.com/revoke"}),
                (200, {}),
            ]
        )
        result = await manager.logout()
        self.assertTrue(result.revocation_confirmed)
        self.assertTrue(result.local_credentials_deleted)
        self.assertIsNone(manager.credentials)
        store.delete.assert_called_once()
        _, url, kwargs = session.calls[1]
        self.assertEqual("https://auth.openai.com/revoke", url)
        self.assertEqual("refresh-secret", kwargs["data"]["token"])

    async def test_logout_network_failure_deletes_locally_and_reports_unconfirmed(self):
        import aiohttp

        manager, store, _session = self.make_manager(
            responses=[aiohttp.ClientConnectionError("offline")]
        )
        result = await manager.logout()
        self.assertFalse(result.revocation_confirmed)
        self.assertTrue(result.local_credentials_deleted)
        store.delete.assert_called_once()
        self.assertIsNone(manager.credentials)


if __name__ == "__main__":
    unittest.main()
