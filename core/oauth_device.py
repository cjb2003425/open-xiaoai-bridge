"""OAuth 2.0 Device Authorization support for OpenAI-compatible backends."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

import aiohttp

from core.utils.base import get_env
from core.utils.logger import logger


class OAuthDeviceConfigError(ValueError):
    """Raised when Device OAuth configuration is incomplete or invalid."""


class OAuthDeviceAuthorizationError(RuntimeError):
    """Raised when device authorization cannot produce a valid token."""

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class _DeviceCodeExpired(Exception):
    pass


@dataclass(frozen=True)
class OAuthDeviceConfig:
    """Validated configuration for an RFC 8628 OAuth client."""

    device_authorization_endpoint: str
    token_endpoint: str
    client_id: str
    scope: str = ""
    audience: str = ""
    token_file: str = "./data/openai-oauth-token.json"
    refresh_leeway: int = 120
    client_secret: str = field(default="", repr=False)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "OAuthDeviceConfig":
        values = {
            key: str(config.get(key, "") or "").strip()
            for key in (
                "device_authorization_endpoint",
                "token_endpoint",
                "client_id",
                "scope",
                "audience",
            )
        }

        for key in (
            "device_authorization_endpoint",
            "token_endpoint",
            "client_id",
        ):
            if not values[key]:
                raise OAuthDeviceConfigError(f"{key} is required")

        for key in ("device_authorization_endpoint", "token_endpoint"):
            parsed = urlparse(values[key])
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise OAuthDeviceConfigError(
                    f"{key} must be an absolute HTTP(S) URL"
                )

        token_file = str(
            config.get("token_file", "./data/openai-oauth-token.json")
            or "./data/openai-oauth-token.json"
        ).strip()
        try:
            refresh_leeway = int(config.get("refresh_leeway", 120))
        except (TypeError, ValueError) as exc:
            raise OAuthDeviceConfigError("refresh_leeway must be an integer") from exc
        if refresh_leeway < 0:
            raise OAuthDeviceConfigError("refresh_leeway must be non-negative")

        runtime_secret = get_env("OPENAI_OAUTH_CLIENT_SECRET")
        client_secret = str(runtime_secret or "").strip()

        return cls(
            **values,
            token_file=token_file,
            refresh_leeway=refresh_leeway,
            client_secret=client_secret,
        )


class DeviceOAuthTokenManager:
    """Obtain and refresh bearer tokens with OAuth Device Authorization."""

    DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

    def __init__(
        self,
        config: OAuthDeviceConfig,
        *,
        session_factory: Callable[[], Any] = aiohttp.ClientSession,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self._session_factory = session_factory
        self._sleep = sleep
        self._clock = clock
        self._session = None
        self._token: dict[str, Any] = self._load_token()
        self._operation_lock = asyncio.Lock()
        self._inflight_task: asyncio.Task[str] | None = None
        self._closed = False

    async def get_access_token(self) -> str:
        if self._closed:
            raise OAuthDeviceAuthorizationError("OAuth token manager is closed")

        if self._is_access_token_valid(self.config.refresh_leeway):
            return str(self._token["access_token"])

        async with self._operation_lock:
            if self._is_access_token_valid(self.config.refresh_leeway):
                return str(self._token["access_token"])
            if self._inflight_task is None or self._inflight_task.done():
                self._inflight_task = asyncio.create_task(self._obtain_access_token())
            task = self._inflight_task

        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._operation_lock:
                    if self._inflight_task is task:
                        self._inflight_task = None

    async def _obtain_access_token(self) -> str:
        if self._token.get("refresh_token"):
            try:
                token = await self._refresh_access_token()
                self._token = token
                self._save_token(token)
                return str(token["access_token"])
            except OAuthDeviceAuthorizationError as exc:
                if exc.error_code in ("invalid_grant", "invalid_token"):
                    self._quarantine_token_file()
                    self._token = {}
                elif self._is_access_token_valid():
                    return str(self._token["access_token"])
                else:
                    raise

        token = await self._authorize_device()
        self._token = token
        self._save_token(token)
        return str(token["access_token"])

    def invalidate_access_token(self) -> None:
        if self._token:
            self._token["expires_at"] = 0.0
            self._save_token(self._token)

    async def close(self) -> None:
        self._closed = True
        task = self._inflight_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._inflight_task = None
        if self._session is not None and not getattr(self._session, "closed", False):
            await self._session.close()
        self._session = None

    def _load_token(self) -> dict[str, Any]:
        path = Path(self.config.token_file)
        if not path.exists():
            return {}
        try:
            token = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(token, dict):
                raise ValueError("token document is not an object")
            if not token.get("access_token") and not token.get("refresh_token"):
                raise ValueError("token document contains no credentials")
            return token
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            invalid_path = self._quarantine_token_file()
            logger.warning(
                "[OpenAI OAuth] Invalid token file moved to "
                f"{invalid_path}: {type(exc).__name__}"
            )
            return {}

    def _save_token(self, token: Mapping[str, Any]) -> None:
        path = Path(self.config.token_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(dict(token), handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            path.chmod(0o600)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                temporary_path.unlink()
            raise

    def _quarantine_token_file(self) -> Path:
        path = Path(self.config.token_file)
        invalid_path = path.with_suffix(path.suffix + ".invalid")
        suffix = 1
        while invalid_path.exists():
            invalid_path = path.with_suffix(path.suffix + f".invalid.{suffix}")
            suffix += 1
        if path.exists():
            path.replace(invalid_path)
        return invalid_path

    async def _authorize_device(self) -> dict[str, Any]:
        while True:
            device = await self._request_device_code()
            verification_uri = str(
                device.get("verification_uri_complete")
                or device.get("verification_uri")
                or ""
            )
            user_code = str(device.get("user_code") or "")
            logger.info(
                "[OpenAI OAuth] "
                f"Open {verification_uri} and enter code {user_code} "
                f"(expires in {device['expires_in']}s)"
            )
            try:
                return await self._poll_device_token(device)
            except _DeviceCodeExpired:
                logger.warning(
                    "[OpenAI OAuth] Device code expired; requesting a new code"
                )

    async def _request_device_code(self) -> dict[str, Any]:
        data = self._base_client_form()
        if self.config.scope:
            data["scope"] = self.config.scope
        if self.config.audience:
            data["audience"] = self.config.audience

        status, body = await self._post_form(
            self.config.device_authorization_endpoint,
            data,
        )
        if status >= 400:
            code = str(body.get("error") or "request_failed")
            raise OAuthDeviceAuthorizationError(
                f"OAuth device authorization request failed: {code}",
                error_code=code,
            )

        required = ("device_code", "user_code", "verification_uri", "expires_in")
        missing = [name for name in required if not body.get(name)]
        if missing:
            raise OAuthDeviceAuthorizationError(
                f"OAuth device response missing {missing[0]}"
            )
        try:
            expires_in = int(body["expires_in"])
            interval = int(body.get("interval", 5))
        except (TypeError, ValueError) as exc:
            raise OAuthDeviceAuthorizationError(
                "OAuth device response has invalid timing values"
            ) from exc
        if expires_in <= 0 or interval <= 0:
            raise OAuthDeviceAuthorizationError(
                "OAuth device response has invalid timing values"
            )
        return {**body, "expires_in": expires_in, "interval": interval}

    async def _poll_device_token(
        self, device: Mapping[str, Any]
    ) -> dict[str, Any]:
        interval = int(device.get("interval", 5))
        deadline = self._clock() + int(device["expires_in"])
        data = self._base_client_form(include_secret=True)
        data.update(
            {
                "grant_type": self.DEVICE_CODE_GRANT_TYPE,
                "device_code": str(device["device_code"]),
            }
        )

        while self._clock() < deadline:
            await self._sleep(interval)
            status, body = await self._post_form(self.config.token_endpoint, data)
            if status < 400:
                return self._normalize_token(body)

            code = str(body.get("error") or "request_failed")
            if code == "authorization_pending":
                continue
            if code == "slow_down":
                interval += 5
                continue
            if code == "expired_token":
                raise _DeviceCodeExpired()
            if code == "access_denied":
                raise OAuthDeviceAuthorizationError(
                    "OAuth authorization was denied",
                    error_code=code,
                )
            raise OAuthDeviceAuthorizationError(
                f"OAuth token request failed: {code}",
                error_code=code,
            )

        raise _DeviceCodeExpired()

    async def _refresh_access_token(self) -> dict[str, Any]:
        data = self._base_client_form(include_secret=True)
        data.update(
            {
                "grant_type": "refresh_token",
                "refresh_token": str(self._token["refresh_token"]),
            }
        )
        if self.config.scope:
            data["scope"] = self.config.scope
        status, body = await self._post_form(self.config.token_endpoint, data)
        if status >= 400:
            code = str(body.get("error") or "request_failed")
            raise OAuthDeviceAuthorizationError(
                f"OAuth refresh failed: {code}",
                error_code=code,
            )
        token = self._normalize_token(body)
        if not token.get("refresh_token"):
            token["refresh_token"] = self._token.get("refresh_token")
        return token

    async def _post_form(
        self, url: str, data: Mapping[str, str]
    ) -> tuple[int, dict[str, Any]]:
        if self._session is None:
            self._session = self._session_factory()
        try:
            async with self._session.post(url, data=dict(data)) as response:
                body = await response.json(content_type=None)
        except ValueError as exc:
            raise OAuthDeviceAuthorizationError(
                "OAuth endpoint returned invalid JSON"
            ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise OAuthDeviceAuthorizationError(
                "OAuth endpoint is temporarily unavailable"
            ) from exc
        if not isinstance(body, dict):
            raise OAuthDeviceAuthorizationError("OAuth endpoint returned invalid JSON")
        return int(response.status), body

    def _normalize_token(self, body: Mapping[str, Any]) -> dict[str, Any]:
        access_token = str(body.get("access_token") or "").strip()
        if not access_token:
            raise OAuthDeviceAuthorizationError(
                "OAuth token response missing access_token"
            )
        try:
            expires_in = int(body.get("expires_in"))
        except (TypeError, ValueError) as exc:
            raise OAuthDeviceAuthorizationError(
                "OAuth token response missing or invalid expires_in"
            ) from exc
        if expires_in <= 0:
            raise OAuthDeviceAuthorizationError(
                "OAuth token response missing or invalid expires_in"
            )

        token = dict(body)
        token["access_token"] = access_token
        token["expires_in"] = expires_in
        token["expires_at"] = self._clock() + expires_in
        return token

    def _base_client_form(self, *, include_secret: bool = False) -> dict[str, str]:
        data = {"client_id": self.config.client_id}
        if include_secret and self.config.client_secret:
            data["client_secret"] = self.config.client_secret
        if self.config.audience:
            data["audience"] = self.config.audience
        return data

    def _is_access_token_valid(self, leeway: int = 0) -> bool:
        access_token = self._token.get("access_token")
        try:
            expires_at = float(self._token.get("expires_at", 0))
        except (TypeError, ValueError):
            return False
        return bool(access_token) and expires_at > self._clock() + leeway
