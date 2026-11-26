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
sys.modules.setdefault("open_xiaoai_server", types.SimpleNamespace())


class FakeStream:
    def __init__(self, events):
        self.events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.events:
            raise StopAsyncIteration
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


def event(event_type, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


def completed_stream(text="reply"):
    return FakeStream(
        [
            event("response.output_text.delta", delta=text),
            event("response.completed"),
        ]
    )


def authentication_error():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(401, request=request)
    return AuthenticationError("expired", response=response, body={})


class FakeClient:
    def __init__(self, responses=()):
        self.responses = SimpleNamespace(create=AsyncMock(side_effect=list(responses)))
        self.close = AsyncMock()


class FakeAuth:
    def __init__(self):
        self.get_access_token = AsyncMock(return_value="oauth-access")
        self.invalidate_access_token = MagicMock()
        self.require_visible_model = AsyncMock(return_value="gpt-visible")
        self.credentials = SimpleNamespace(selected_model="gpt-visible")
        self.close = AsyncMock()


class QuotaFailure(Exception):
    status_code = 429
    body = {"error": {"code": "usage_limit_reached"}}


class ChatGPTManagerMixin:
    def load_manager(self):
        sys.modules.pop("core.chatgpt", None)
        manager = importlib.import_module("core.chatgpt").ChatGPTManager
        manager._initialized = True
        manager._enabled = True
        manager._auth = FakeAuth()
        manager._client = None
        manager._client_lock = asyncio.Lock()
        manager._model = "gpt-visible"
        manager._system_prompt = "Be concise"
        manager._timeout = 42
        manager._history_max_messages = 20
        manager._history = []
        manager._response_events = {}
        manager._response_texts = {}
        manager._response_tts_speakers = {}
        manager._tts_provider = None
        manager._tts_speaker = "xiaoai"
        manager._tts_speed = 1.0
        manager._rule_prompt = ""
        manager._rule_prompt_for_skill = ""
        manager.last_error = None
        return manager


class ChatGPTClientTest(ChatGPTManagerMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = self.load_manager()

    async def test_client_uses_async_oauth_credential_official_base_and_zero_retries(self):
        client = FakeClient()
        with patch("core.chatgpt.AsyncOpenAI", return_value=client) as constructor:
            first = await self.manager._ensure_client()
            second = await self.manager._ensure_client()

        self.assertIs(first, second)
        constructor.assert_called_once_with(
            api_key=self.manager._auth.get_access_token,
            base_url="https://api.openai.com/v1",
            timeout=42,
            max_retries=0,
        )

    async def test_response_request_sends_full_history_instructions_store_false_stream_true(self):
        client = FakeClient([completed_stream("new reply")])
        self.manager._client = client
        self.manager._history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]

        response = await self.manager._request_response("new question")

        self.assertEqual("new reply", response)
        client.responses.create.assert_awaited_once_with(
            model="gpt-visible",
            input=[
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "new question"},
            ],
            instructions="Be concise",
            store=False,
            stream=True,
        )

    async def test_request_omits_tools_and_all_siwc_unsupported_fields(self):
        client = FakeClient([completed_stream()])
        self.manager._client = client
        await self.manager._request_response("hello")
        kwargs = client.responses.create.await_args.kwargs
        for forbidden in (
            "tools",
            "temperature",
            "max_output_tokens",
            "previous_response_id",
            "conversation",
            "service_tier",
        ):
            self.assertNotIn(forbidden, kwargs)

    async def test_completed_stream_appends_turn_and_returns_joined_text(self):
        client = FakeClient(
            [
                FakeStream(
                    [
                        event("response.output_text.delta", delta="part "),
                        event("response.output_text.delta", delta="two"),
                        event("response.completed"),
                    ]
                )
            ]
        )
        self.manager._client = client
        self.assertEqual("part two", await self.manager._request_response("hello"))
        self.assertEqual(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "part two"},
            ],
            self.manager._history,
        )

    async def test_text_then_failed_does_not_append_or_return_partial(self):
        client = FakeClient(
            [FakeStream([event("response.output_text.delta", delta="partial"), event("response.failed")])]
        )
        self.manager._client = client
        with self.assertRaisesRegex(Exception, "failed"):
            await self.manager._request_response("hello")
        self.assertEqual([], self.manager._history)

    async def test_text_then_eof_without_completed_fails(self):
        self.manager._client = FakeClient(
            [FakeStream([event("response.output_text.delta", delta="partial")])]
        )
        with self.assertRaisesRegex(Exception, "without completion"):
            await self.manager._request_response("hello")
        self.assertEqual([], self.manager._history)

    async def test_incomplete_stream_fails(self):
        self.manager._client = FakeClient([FakeStream([event("response.incomplete")])])
        with self.assertRaisesRegex(Exception, "incomplete"):
            await self.manager._request_response("hello")

    async def test_new_wake_reset_clears_history(self):
        self.manager._history = [{"role": "user", "content": "old"}]
        self.manager.reset_session()
        self.assertEqual([], self.manager._history)

    async def test_quota_errors_become_short_user_message(self):
        self.manager._client = FakeClient([QuotaFailure("limit")])
        response = await self.manager._request_response("hello")
        self.assertIn("额度", response)
        self.assertLess(len(response), 40)
        self.assertEqual([], self.manager._history)

    async def test_first_authentication_error_invalidates_and_retries_once(self):
        self.manager._client = FakeClient([authentication_error(), completed_stream("ok")])
        self.assertEqual("ok", await self.manager._request_response("hello"))
        self.manager._auth.invalidate_access_token.assert_called_once_with()
        self.assertEqual(2, self.manager._client.responses.create.await_count)

    async def test_second_authentication_error_escapes(self):
        self.manager._client = FakeClient([authentication_error(), authentication_error()])
        with self.assertRaises(AuthenticationError):
            await self.manager._request_response("hello")
        self.assertEqual([], self.manager._history)

    async def test_concurrent_requests_reuse_one_client(self):
        client = FakeClient()
        with patch("core.chatgpt.AsyncOpenAI", return_value=client) as constructor:
            clients = await asyncio.gather(
                self.manager._ensure_client(), self.manager._ensure_client()
            )
        self.assertEqual([client, client], clients)
        constructor.assert_called_once()

    async def test_close_cancels_waiters_and_closes_sdk_and_auth(self):
        client = FakeClient()
        waiter = asyncio.get_running_loop().create_future()
        self.manager._client = client
        self.manager._response_events = {"run": waiter}
        await self.manager.close()
        self.assertTrue(waiter.cancelled())
        client.close.assert_awaited_once()
        self.manager._auth.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
