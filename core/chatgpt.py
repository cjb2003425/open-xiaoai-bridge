"""Tool-free ChatGPT plan backend using official SIWC and Responses."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from openai import AsyncOpenAI, AuthenticationError

from core.chatgpt_auth import ChatGPTAuthConfig, ChatGPTAuthManager
from core.utils.base import get_env
from core.utils.config import ConfigManager
from core.utils.logger import logger


class ChatGPTResponseError(RuntimeError):
    pass


class ChatGPTManager:
    """Stream plain text responses while keeping history inside one wake."""

    _initialized = False
    _reload_listener_registered = False
    _enabled = False
    _auth: ChatGPTAuthManager | None = None
    _client: AsyncOpenAI | None = None
    _client_lock = asyncio.Lock()
    _model = ""
    _system_prompt = ""
    _timeout = 120
    _history_max_messages = 20
    _history: list[dict[str, str]] = []
    _tts_provider: str | None = None
    _tts_speaker: str | None = None
    _tts_speed = 1.0
    _rule_prompt = ""
    _rule_prompt_for_skill = ""
    _response_events: dict[str, asyncio.Future] = {}
    _response_texts: dict[str, str] = {}
    _response_tts_speakers: dict[str, str | None] = {}
    last_error: str | None = None

    @classmethod
    def initialize_from_config(cls, enabled: bool | None = None):
        cls.reload_from_config(enabled=enabled)
        cls._initialized = True

    @classmethod
    def reload_from_config(cls, enabled: bool | None = None):
        manager = ConfigManager.instance()
        if not cls._reload_listener_registered:
            manager.add_reload_listener(lambda _old, _new: cls.reload_from_config())
            cls._reload_listener_registered = True
        config = manager.get_app_config("chatgpt", {})
        if enabled is None:
            env_enabled = get_env("CHATGPT_ENABLE")
            cls._enabled = bool(
                env_enabled and env_enabled.lower() in ("1", "true", "yes")
            )
        else:
            cls._enabled = enabled
        cls._model = str(config.get("model", "") or "").strip()
        cls._system_prompt = str(config.get("system_prompt", "") or "").strip()
        cls._timeout = int(config.get("response_timeout", 120))
        cls._history_max_messages = max(
            0, int(config.get("history_max_messages", 20))
        )
        provider = str(config.get("tts_provider", "") or "").strip().lower()
        cls._tts_provider = provider or None
        speaker = config.get("tts_speaker")
        cls._tts_speaker = str(speaker) if speaker else None
        cls._tts_speed = float(config.get("tts_speed", 1.0))
        cls._rule_prompt = str(config.get("rule_prompt", "") or "")
        cls._rule_prompt_for_skill = str(config.get("rule_prompt_for_skill", "") or "")
        cls._auth = ChatGPTAuthManager(ChatGPTAuthConfig.from_mapping(config))

    @classmethod
    async def connect(cls) -> bool:
        if not cls._initialized:
            cls.initialize_from_config()
        if not cls._enabled or cls._auth is None:
            return False
        cls._model = await cls._auth.require_visible_model(cls._model)
        await cls._ensure_client()
        return True

    @classmethod
    async def _ensure_client(cls) -> AsyncOpenAI:
        if cls._client is not None:
            return cls._client
        async with cls._client_lock:
            if cls._client is not None:
                return cls._client
            if cls._auth is None:
                raise RuntimeError("ChatGPT authentication manager is unavailable")
            cls._client = AsyncOpenAI(
                api_key=cls._auth.get_access_token,
                base_url=ChatGPTAuthConfig.API_BASE_URL,
                timeout=cls._timeout,
                max_retries=0,
            )
            return cls._client

    @classmethod
    async def close(cls):
        for waiter in list(cls._response_events.values()):
            if not waiter.done():
                waiter.cancel()
        cls._response_events.clear()
        cls._response_texts.clear()
        cls._response_tts_speakers.clear()
        client = cls._client
        cls._client = None
        if client is not None:
            await client.close()
        if cls._auth is not None:
            await cls._auth.close()

    @classmethod
    def is_enabled(cls) -> bool:
        if not cls._initialized:
            cls.initialize_from_config()
        return cls._enabled

    @classmethod
    def is_connected(cls) -> bool:
        return cls.is_enabled() and cls._auth is not None

    @classmethod
    def reset_session(cls):
        cls._history = []

    @classmethod
    async def send(cls, text: str, wait_response: bool = False) -> str | None:
        run_id = await cls._send_and_track(text)
        if run_id is None:
            return None
        if not wait_response:
            asyncio.create_task(cls._wait_response(run_id))
            return run_id
        try:
            return await cls._wait_response(run_id)
        finally:
            cls._response_tts_speakers.pop(run_id, None)

    @classmethod
    async def send_and_play_reply(
        cls, text: str, wait_response: bool = False
    ) -> str | None:
        run_id = await cls._send_and_track(text)
        if run_id is None:
            return None
        if not wait_response:
            asyncio.create_task(cls._wait_and_play_response(run_id))
            return run_id
        try:
            response = await cls._wait_response(run_id)
            if response:
                await cls._play_response_with_tts(response)
            return response
        finally:
            cls._response_tts_speakers.pop(run_id, None)

    @classmethod
    async def _send_and_track(cls, text: str) -> str | None:
        if not cls._initialized:
            cls.initialize_from_config()
        if not cls._enabled:
            logger.warning("[ChatGPT] send called but backend is disabled")
            return None
        run_id = str(uuid.uuid4())
        cls._response_events[run_id] = asyncio.get_running_loop().create_future()
        cls._response_texts[run_id] = ""
        cls._response_tts_speakers[run_id] = cls._tts_speaker
        logger.user_speech(text, module="ChatGPT")
        asyncio.create_task(cls._run_response(run_id, text))
        return run_id

    @classmethod
    async def _run_response(cls, run_id: str, text: str):
        try:
            response = await cls._request_response(text)
            if response:
                cls._response_texts[run_id] = response
                logger.ai_response(response, module="ChatGPT")
        except Exception as exc:
            cls.last_error = f"{type(exc).__name__}: {exc}"
            logger.error(f"[ChatGPT] Response failed: {cls.last_error}")
        finally:
            waiter = cls._response_events.get(run_id)
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

    @classmethod
    async def _wait_response(cls, run_id: str) -> str | None:
        waiter = cls._response_events.get(run_id)
        if waiter is None:
            return None
        try:
            await asyncio.wait_for(waiter, timeout=cls._timeout)
            return cls._response_texts.get(run_id) or None
        except asyncio.TimeoutError:
            return None
        finally:
            cls._response_events.pop(run_id, None)
            cls._response_texts.pop(run_id, None)

    @classmethod
    async def _request_response(cls, text: str) -> str | None:
        history = (
            cls._history[-cls._history_max_messages :]
            if cls._history_max_messages
            else []
        )
        request: dict[str, Any] = {
            "model": cls._model,
            "input": [*history, {"role": "user", "content": text}],
            "store": False,
            "stream": True,
        }
        if cls._system_prompt:
            request["instructions"] = cls._system_prompt

        client = await cls._ensure_client()
        for attempt in range(2):
            try:
                stream = await client.responses.create(**request)
                response_text = await cls._consume_stream(stream)
                if response_text:
                    cls._append_history(text, response_text)
                return response_text
            except AuthenticationError:
                if attempt or cls._auth is None:
                    raise
                cls._auth.invalidate_access_token()
            except Exception as exc:
                if cls._is_quota_error(exc):
                    cls.last_error = f"{type(exc).__name__}: quota unavailable"
                    return "ChatGPT 额度暂时不可用，请稍后再试。"
                raise
        return None

    @classmethod
    async def _consume_stream(cls, stream) -> str | None:
        parts: list[str] = []
        completed = False
        async for stream_event in stream:
            event_type = str(getattr(stream_event, "type", "") or "")
            if event_type == "response.output_text.delta":
                delta = getattr(stream_event, "delta", "")
                if isinstance(delta, str):
                    parts.append(delta)
            elif event_type == "response.completed":
                completed = True
            elif event_type == "response.failed":
                raise ChatGPTResponseError("ChatGPT response failed")
            elif event_type == "response.incomplete":
                raise ChatGPTResponseError("ChatGPT response was incomplete")
        if not completed:
            raise ChatGPTResponseError("ChatGPT stream ended without completion")
        text = "".join(parts).strip()
        return text or None

    @classmethod
    def _append_history(cls, user_text: str, response_text: str):
        if cls._history_max_messages <= 0:
            return
        cls._history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": response_text},
            ]
        )
        if len(cls._history) > cls._history_max_messages:
            del cls._history[: len(cls._history) - cls._history_max_messages]

    @staticmethod
    def _is_quota_error(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "body", None)
        code = ""
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                code = str(error.get("code", "") or "")
        return status == 429 or code in {
            "usage_limit_reached",
            "rate_limit_exceeded",
            "subscription_sharing_usage_limit_reached",
        }

    @classmethod
    async def _wait_and_play_response(cls, run_id: str):
        try:
            response = await cls._wait_response(run_id)
            if response:
                await cls._play_response_with_tts(
                    response,
                    tts_speaker=cls._response_tts_speakers.get(run_id),
                )
        finally:
            cls._response_tts_speakers.pop(run_id, None)

    @classmethod
    async def _play_response_with_tts(
        cls,
        text: str,
        tts_speaker: str | None = None,
        playback_token: int | None = None,
    ):
        from core.services.tts.router import TTSRouter

        await TTSRouter.play(
            text,
            configured_provider=cls._tts_provider,
            tts_speaker=tts_speaker or cls._tts_speaker,
            tts_speed=cls._tts_speed,
            playback_token=playback_token,
            log_prefix="ChatGPT",
        )
