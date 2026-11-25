import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import AuthenticationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeCompletion:
    def __init__(self, body):
        self.body = body

    def model_dump(self):
        return self.body


class FakeOpenAIClient:
    def __init__(self, responses=None):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=responses or [])
            )
        )
        self.close = AsyncMock()


class FakeOAuthManager:
    def __init__(self, _config=None):
        self.get_access_token = AsyncMock(return_value="oauth-access")
        self.invalidate_access_token = MagicMock()
        self.close = AsyncMock()


def authentication_error():
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return AuthenticationError("invalid token", response=response, body={})


class OpenAIManagerMixin:
    def load_manager(self):
        sys.modules.setdefault("open_xiaoai_server", types.SimpleNamespace())
        sys.modules.pop("core.openai", None)
        manager = importlib.import_module("core.openai").OpenAIManager
        manager._initialized = True
        manager._enabled = True
        manager._base_url = "https://api.example.com/v1"
        manager._api_key = "api-secret"
        manager._auth_type = "api_key"
        manager._oauth_config = None
        manager._oauth_manager = None
        manager._client = None
        manager._client_fingerprint = None
        manager._model = "test-model"
        manager._session_key = "agent:default:open-xiaoai-bridge"
        manager._session_header = "X-Hermes-Session-Key"
        manager._system_prompt = "system rule"
        manager._temperature = 0.25
        manager._max_tokens = 321
        manager._timeout = 42
        manager._history_max_messages = 20
        manager._extra_body = {"vendor_option": "kept"}
        manager._sessions = {}
        manager._response_events = {}
        manager._response_texts = {}
        manager._response_tts_speakers = {}
        return manager


class OpenAIConfigTest(OpenAIManagerMixin, unittest.TestCase):
    def setUp(self):
        self.manager = self.load_manager()

    def test_runtime_api_key_overrides_config_secret(self):
        with patch("core.openai.get_env", return_value="runtime-secret"):
            self.assertEqual(
                "runtime-secret",
                self.manager._configured_api_key({"api_key": "config-secret"}),
            )

    def test_config_api_key_remains_fallback(self):
        with patch("core.openai.get_env", return_value=None):
            self.assertEqual(
                "config-secret",
                self.manager._configured_api_key({"api_key": "config-secret"}),
            )

    def test_explicit_empty_runtime_api_key_disables_config_secret(self):
        with patch("core.openai.get_env", return_value=""):
            self.assertEqual(
                "",
                self.manager._configured_api_key({"api_key": "config-secret"}),
            )


class OpenAIClientTest(OpenAIManagerMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = self.load_manager()

    async def test_api_key_client_is_configured_once_and_reused(self):
        client = FakeOpenAIClient()
        with patch("core.openai.AsyncOpenAI", return_value=client) as constructor:
            first = await self.manager._ensure_client()
            second = await self.manager._ensure_client()

        self.assertIs(client, first)
        self.assertIs(first, second)
        constructor.assert_called_once_with(
            api_key="api-secret",
            base_url="https://api.example.com/v1",
            timeout=42,
            default_headers={
                "X-Hermes-Session-Key": "agent:default:open-xiaoai-bridge"
            },
        )

    async def test_oauth_client_uses_async_credential_callable(self):
        client = FakeOpenAIClient()
        oauth = FakeOAuthManager()
        self.manager._auth_type = "oauth_device"
        self.manager._oauth_config = object()

        with (
            patch("core.openai.DeviceOAuthTokenManager", return_value=oauth),
            patch("core.openai.AsyncOpenAI", return_value=client) as constructor,
        ):
            self.assertIs(client, await self.manager._ensure_client())

        kwargs = constructor.call_args.kwargs
        self.assertIs(oauth.get_access_token, kwargs["api_key"])
        self.assertEqual("https://api.example.com/v1", kwargs["base_url"])

    async def test_configuration_fingerprint_change_closes_old_client(self):
        old_client = FakeOpenAIClient()
        new_client = FakeOpenAIClient()
        with patch(
            "core.openai.AsyncOpenAI", side_effect=[old_client, new_client]
        ):
            self.assertIs(old_client, await self.manager._ensure_client())
            self.manager._base_url = "https://replacement.example.com/v1"
            self.assertIs(new_client, await self.manager._ensure_client())

        old_client.close.assert_awaited_once()

    async def test_close_cancels_waiters_and_closes_sdk_and_oauth(self):
        client = FakeOpenAIClient()
        oauth = FakeOAuthManager()
        waiter = asyncio.get_running_loop().create_future()
        self.manager._client = client
        self.manager._oauth_manager = oauth
        self.manager._response_events = {"run": waiter}

        await self.manager.close()

        self.assertTrue(waiter.cancelled())
        client.close.assert_awaited_once()
        oauth.close.assert_awaited_once()
        self.assertIsNone(self.manager._client)
        self.assertIsNone(self.manager._oauth_manager)

    async def test_completion_maps_parameters_and_updates_history(self):
        client = FakeOpenAIClient(
            [
                FakeCompletion(
                    {"choices": [{"message": {"content": "  sdk reply  "}}]}
                )
            ]
        )
        self.manager._ensure_client = AsyncMock(return_value=client)

        response = await self.manager._request_chat_completion("hello")

        self.assertEqual("sdk reply", response)
        client.chat.completions.create.assert_awaited_once_with(
            model="test-model",
            messages=[
                {"role": "system", "content": "system rule"},
                {"role": "user", "content": "hello"},
            ],
            stream=False,
            temperature=0.25,
            max_tokens=321,
            extra_body={"vendor_option": "kept"},
        )
        self.assertEqual(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "sdk reply"},
            ],
            self.manager._sessions[self.manager._session_key],
        )

    async def test_structured_completion_content_is_preserved(self):
        client = FakeOpenAIClient(
            [
                FakeCompletion(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {"type": "text", "text": "part one"},
                                        {"type": "image", "url": "ignored"},
                                        {"type": "text", "text": " + two"},
                                    ]
                                }
                            }
                        ]
                    }
                )
            ]
        )
        self.manager._ensure_client = AsyncMock(return_value=client)

        self.assertEqual(
            "part one + two",
            await self.manager._request_chat_completion("hello"),
        )

    async def test_oauth_authentication_error_invalidates_and_retries_once(self):
        oauth = FakeOAuthManager()
        client = FakeOpenAIClient(
            [
                authentication_error(),
                FakeCompletion(
                    {"choices": [{"message": {"content": "recovered"}}]}
                ),
            ]
        )
        self.manager._auth_type = "oauth_device"
        self.manager._oauth_manager = oauth
        self.manager._ensure_client = AsyncMock(return_value=client)

        self.assertEqual(
            "recovered", await self.manager._request_chat_completion("hello")
        )
        oauth.invalidate_access_token.assert_called_once_with()
        self.assertEqual(2, client.chat.completions.create.await_count)

    async def test_second_oauth_authentication_error_is_not_retried(self):
        oauth = FakeOAuthManager()
        client = FakeOpenAIClient([authentication_error(), authentication_error()])
        self.manager._auth_type = "oauth_device"
        self.manager._oauth_manager = oauth
        self.manager._ensure_client = AsyncMock(return_value=client)

        with self.assertRaises(AuthenticationError):
            await self.manager._request_chat_completion("hello")
        self.assertEqual(2, client.chat.completions.create.await_count)

    async def test_api_key_authentication_error_does_not_use_oauth_retry(self):
        client = FakeOpenAIClient([authentication_error()])
        self.manager._ensure_client = AsyncMock(return_value=client)

        with self.assertRaises(AuthenticationError):
            await self.manager._request_chat_completion("hello")
        self.assertEqual(1, client.chat.completions.create.await_count)


if __name__ == "__main__":
    unittest.main()
