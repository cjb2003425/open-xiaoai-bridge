import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
