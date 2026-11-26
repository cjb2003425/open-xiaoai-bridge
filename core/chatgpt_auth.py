"""Official Sign in with ChatGPT authentication primitives.

This module deliberately keeps ChatGPT credentials separate from the
OpenAI-compatible API-key backend.  The protocol implementation is layered on
top of these validated, redacted, and atomically persisted data types.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
import jwt
from aiohttp import web


class ChatGPTAuthError(RuntimeError):
    """A safe-to-display SIWC configuration or authentication error."""

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ChatGPTAuthConfig:
    """Validated SIWC settings with non-overridable official endpoints."""

    ISSUER = "https://auth.openai.com"
    AUTHORIZATION_ENDPOINT = f"{ISSUER}/api/accounts/authorize"
    TOKEN_ENDPOINT = f"{ISSUER}/api/accounts/oauth/token"
    JWKS_ENDPOINT = f"{ISSUER}/.well-known/jwks.json"
    API_BASE_URL = "https://api.openai.com/v1"
    REQUIRED_SCOPES = (
        "openid",
        "profile",
        "email",
        "offline_access",
        "resource.invoke",
        "chatgpt.tokens.use.direct",
    )

    issuer: str = ISSUER
    authorization_endpoint: str = AUTHORIZATION_ENDPOINT
    token_endpoint: str = TOKEN_ENDPOINT
    jwks_endpoint: str = JWKS_ENDPOINT
    api_base_url: str = API_BASE_URL
    resource: str = API_BASE_URL
    callback_host: str = "127.0.0.1"
    callback_port: int = 1455
    callback_path: str = "/auth/callback"
    credential_file: str = "./data/chatgpt-siwc.json"
    scopes: tuple[str, ...] = REQUIRED_SCOPES
    login_timeout: int = 300
    refresh_leeway: int = 120
    agent_name: str = "open-xiaoai-bridge"

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.callback_host}:{self.callback_port}{self.callback_path}"

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ChatGPTAuthConfig":
        callback_host = str(config.get("callback_host", "127.0.0.1") or "").strip()
        callback_path = str(config.get("callback_path", "/auth/callback") or "").strip()
        if callback_host != "127.0.0.1":
            raise ChatGPTAuthError("ChatGPT callback_host must be 127.0.0.1")
        if callback_path != "/auth/callback":
            raise ChatGPTAuthError("ChatGPT callback_path must be /auth/callback")

        callback_port = cls._integer(config, "callback_port", 1455)
        if not 1 <= callback_port <= 65535:
            raise ChatGPTAuthError("ChatGPT callback_port must be between 1 and 65535")
        login_timeout = cls._integer(config, "login_timeout", 300)
        if login_timeout <= 0:
            raise ChatGPTAuthError("ChatGPT login_timeout must be positive")
        refresh_leeway = cls._integer(config, "refresh_leeway", 120)
        if refresh_leeway < 0:
            raise ChatGPTAuthError("ChatGPT refresh_leeway must be non-negative")

        credential_file = str(
            config.get("credential_file", "./data/chatgpt-siwc.json")
            or "./data/chatgpt-siwc.json"
        ).strip()
        agent_name = str(
            config.get("agent_name", "open-xiaoai-bridge")
            or "open-xiaoai-bridge"
        ).strip()
        if not credential_file:
            raise ChatGPTAuthError("ChatGPT credential_file is required")
        if not agent_name:
            raise ChatGPTAuthError("ChatGPT agent_name is required")

        return cls(
            callback_host=callback_host,
            callback_port=callback_port,
            callback_path=callback_path,
            credential_file=credential_file,
            login_timeout=login_timeout,
            refresh_leeway=refresh_leeway,
            agent_name=agent_name,
        )

    @staticmethod
    def _integer(config: Mapping[str, Any], key: str, default: int) -> int:
        try:
            return int(config.get(key, default))
        except (TypeError, ValueError) as exc:
            raise ChatGPTAuthError(f"ChatGPT {key} must be an integer") from exc


@dataclass(frozen=True)
class ChatGPTCredentials:
    """A validated SIWC credential record whose secrets never appear in repr."""

    issued_client_id: str
    ext_agent_host_id: str
    issuer: str
    subject: str
    email: str
    id_token: str = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    token_type: str = "Bearer"
    expires_at: float = 0.0
    scopes: tuple[str, ...] = ()
    selected_model: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ChatGPTCredentials":
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
        values = {key: str(data.get(key, "") or "").strip() for key in required}
        for key, value in values.items():
            if not value:
                raise ChatGPTAuthError(f"Stored ChatGPT credentials are missing {key}")
        if values["issuer"] != ChatGPTAuthConfig.ISSUER:
            raise ChatGPTAuthError("Stored ChatGPT credentials have an invalid issuer")

        raw_scopes = data.get("scopes", ())
        if isinstance(raw_scopes, str):
            scopes = tuple(item for item in raw_scopes.split() if item)
        elif isinstance(raw_scopes, (list, tuple, set)):
            scopes = tuple(str(item).strip() for item in raw_scopes if str(item).strip())
        else:
            raise ChatGPTAuthError("Stored ChatGPT credentials have invalid scopes")
        if "chatgpt.tokens.use.direct" not in scopes:
            raise ChatGPTAuthError("Stored ChatGPT credentials lack direct usage scope")

        try:
            expires_at = float(data.get("expires_at", 0))
        except (TypeError, ValueError) as exc:
            raise ChatGPTAuthError("Stored ChatGPT credentials have invalid expiry") from exc
        if expires_at <= 0:
            raise ChatGPTAuthError("Stored ChatGPT credentials have invalid expiry")

        token_type = str(data.get("token_type", "Bearer") or "Bearer").strip()
        if token_type.lower() != "bearer":
            raise ChatGPTAuthError("Stored ChatGPT credentials have invalid token type")

        return cls(
            **values,
            token_type=token_type,
            expires_at=expires_at,
            scopes=scopes,
            selected_model=str(data.get("selected_model", "") or "").strip(),
        )

    def to_mapping(self) -> dict[str, Any]:
        data = asdict(self)
        data["scopes"] = list(self.scopes)
        return data


class ChatGPTCredentialStore:
    """Atomic, owner-only persistence for SIWC credentials."""

    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> ChatGPTCredentials | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("credential document is not an object")
            return ChatGPTCredentials.from_mapping(data)
        except (OSError, ValueError, json.JSONDecodeError, ChatGPTAuthError):
            self.quarantine()
            return None

    def save(self, credentials: ChatGPTCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    credentials.to_mapping(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            self.path.chmod(0o600)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                temporary_path.unlink()
            raise

    def delete(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def quarantine(self) -> Path | None:
        if not self.path.exists():
            return None
        destination = self.path.with_name(f"{self.path.name}.invalid")
        suffix = 1
        while destination.exists():
            destination = self.path.with_name(
                f"{self.path.name}.invalid.{suffix}"
            )
            suffix += 1
        self.path.replace(destination)
        return destination


@dataclass(frozen=True)
class ChatGPTModel:
    slug: str
    display_name: str


@dataclass(frozen=True)
class LogoutResult:
    revocation_confirmed: bool
    local_credentials_deleted: bool


class ChatGPTAuthManager:
    """Run official SIWC login without exposing credentials to callers."""

    DYNAMIC_CLIENT_ID = "dynamic_agent_client"

    def __init__(
        self,
        config: ChatGPTAuthConfig,
        *,
        store: ChatGPTCredentialStore | None = None,
        session_factory: Callable[[], aiohttp.ClientSession] = aiohttp.ClientSession,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self.store = store or ChatGPTCredentialStore(config.credential_file)
        self._session_factory = session_factory
        self._clock = clock
        self._session: aiohttp.ClientSession | None = None
        self._credentials = self.store.load()
        self._callback_runner: web.AppRunner | None = None
        self._login_task: asyncio.Task | None = None
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[ChatGPTCredentials] | None = None
        self._force_refresh = False

    @property
    def credentials(self) -> ChatGPTCredentials | None:
        return self._credentials

    async def login(
        self,
        url_presenter: Callable[[str], None],
        model_selector: Callable[[Sequence[ChatGPTModel]], str],
    ) -> ChatGPTCredentials:
        """Authorize once and replace saved credentials only after validation."""
        old_credentials = self._credentials
        ext_agent_host_id = (
            old_credentials.ext_agent_host_id
            if old_credentials is not None
            else str(uuid.uuid4())
        )
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        challenge = self._pkce_challenge(code_verifier)
        authorization_url = self._build_authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            ext_agent_host_id=ext_agent_host_id,
            credentials=old_credentials,
        )

        code, callback_client_id = await self._await_callback(
            state,
            on_ready=lambda: url_presenter(authorization_url),
        )
        issued_client_id = self._validate_callback_client_id(callback_client_id)
        token_body = await self._exchange_code(
            code=code,
            code_verifier=code_verifier,
            issued_client_id=issued_client_id,
        )
        token = self._normalize_token_response(token_body)
        jwks = await self._fetch_jwks()
        claims = self._validate_id_token(
            token["id_token"],
            jwks,
            issued_client_id=issued_client_id,
            expected_nonce=nonce,
        )
        if old_credentials is not None and claims["sub"] != old_credentials.subject:
            raise ChatGPTAuthError("ChatGPT reauthorization returned a different account")

        models = await self._list_models_with_token(token["access_token"])
        selected_model = str(model_selector(models) or "").strip()
        visible_slugs = {model.slug for model in models}
        if selected_model not in visible_slugs:
            raise ChatGPTAuthError("Selected ChatGPT model is not available")

        credentials = ChatGPTCredentials.from_mapping(
            {
                "issued_client_id": issued_client_id,
                "ext_agent_host_id": ext_agent_host_id,
                "issuer": claims["iss"],
                "subject": claims["sub"],
                "email": claims["email"],
                "id_token": token["id_token"],
                "access_token": token["access_token"],
                "refresh_token": token["refresh_token"],
                "token_type": token["token_type"],
                "expires_at": token["expires_at"],
                "scopes": token["scopes"],
                "selected_model": selected_model,
            }
        )
        self.store.save(credentials)
        self._credentials = credentials
        return credentials

    @staticmethod
    def _pkce_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _build_authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        ext_agent_host_id: str,
        credentials: ChatGPTCredentials | None = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": (
                credentials.issued_client_id
                if credentials is not None
                else self.DYNAMIC_CLIENT_ID
            ),
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "resource": self.config.resource,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "ext_agent_host_id": ext_agent_host_id,
        }
        if credentials is None:
            params["agent_name_hint"] = self.config.agent_name
        else:
            params["id_token_hint"] = credentials.id_token
        return f"{self.config.authorization_endpoint}?{urlencode(params)}"

    @staticmethod
    def _redact_authorization_url(url: str) -> str:
        parts = urlsplit(url)
        query = [
            (key, "[REDACTED]" if key == "id_token_hint" else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _validate_callback_request(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        expected_state: str,
    ) -> tuple[str, str]:
        if path != self.config.callback_path:
            raise ChatGPTAuthError("Invalid ChatGPT callback path")
        if not secrets.compare_digest(str(query.get("state", "")), expected_state):
            raise ChatGPTAuthError("Invalid OAuth state")
        if query.get("error"):
            raise ChatGPTAuthError("ChatGPT authorization was denied")
        code = str(query.get("code", "") or "").strip()
        issued_client_id = str(query.get("client_id", "") or "").strip()
        if not code:
            raise ChatGPTAuthError("ChatGPT callback is incomplete")
        return code, issued_client_id

    async def _await_callback(
        self,
        expected_state: str,
        on_ready: Callable[[], None] | None = None,
    ) -> tuple[str, str]:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[tuple[str, str]] = loop.create_future()

        async def callback(request: web.Request) -> web.Response:
            try:
                callback_result = self._validate_callback_request(
                    path=request.path,
                    query=request.query,
                    expected_state=expected_state,
                )
            except ChatGPTAuthError as exc:
                if not result.done():
                    result.set_exception(exc)
                status = 404 if request.path != self.config.callback_path else 400
                return web.Response(status=status, text="ChatGPT sign-in failed. You may close this tab.")
            if not result.done():
                result.set_result(callback_result)
            return web.Response(text="ChatGPT sign-in completed. You may close this tab.")

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", callback)
        runner = web.AppRunner(app)
        self._callback_runner = runner
        try:
            await runner.setup()
            site = web.TCPSite(
                runner,
                host=self.config.callback_host,
                port=self.config.callback_port,
            )
            await site.start()
            if on_ready is not None:
                on_ready()
            return await asyncio.wait_for(result, timeout=self.config.login_timeout)
        except asyncio.TimeoutError as exc:
            raise ChatGPTAuthError("ChatGPT sign-in callback timed out") from exc
        finally:
            await runner.cleanup()
            self._callback_runner = None

    def _validate_callback_client_id(self, callback_client_id: str) -> str:
        callback_client_id = str(callback_client_id or "").strip()
        if self._credentials is not None:
            if (
                callback_client_id
                and callback_client_id != self._credentials.issued_client_id
            ):
                raise ChatGPTAuthError(
                    "ChatGPT callback client ID changed during reauthorization"
                )
            return callback_client_id or self._credentials.issued_client_id
        if not callback_client_id:
            raise ChatGPTAuthError("ChatGPT callback omitted the issued client ID")
        return callback_client_id

    async def _exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        issued_client_id: str,
    ) -> dict[str, Any]:
        session = self._get_session()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": issued_client_id,
            "redirect_uri": self.config.redirect_uri,
            "code_verifier": code_verifier,
            "resource": self.config.resource,
        }
        try:
            async with session.post(self.config.token_endpoint, data=data) as response:
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT token exchange failed") from exc
        if response.status >= 400 or not isinstance(body, dict):
            raise ChatGPTAuthError("ChatGPT token exchange was rejected")
        return body

    def _normalize_token_response(self, body: Mapping[str, Any]) -> dict[str, Any]:
        values = {
            key: str(body.get(key, "") or "").strip()
            for key in ("id_token", "access_token", "refresh_token")
        }
        for key, value in values.items():
            if not value:
                raise ChatGPTAuthError(f"ChatGPT token response is missing {key}")
        raw_scope = body.get("scope", "")
        scopes = tuple(str(raw_scope).split())
        if "chatgpt.tokens.use.direct" not in scopes:
            raise ChatGPTAuthError("ChatGPT token response lacks direct usage scope")
        try:
            expires_in = int(body.get("expires_in", 0))
        except (TypeError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT token response has invalid expiry") from exc
        if expires_in <= 0:
            raise ChatGPTAuthError("ChatGPT token response has invalid expiry")
        token_type = str(body.get("token_type", "Bearer") or "Bearer").strip()
        if token_type.lower() != "bearer":
            raise ChatGPTAuthError("ChatGPT token response has invalid token type")
        return {
            **values,
            "token_type": token_type,
            "expires_at": self._clock() + expires_in,
            "scopes": scopes,
        }

    async def _fetch_jwks(self) -> dict[str, Any]:
        session = self._get_session()
        try:
            async with session.get(self.config.jwks_endpoint) as response:
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT identity keys are unavailable") from exc
        if response.status >= 400 or not isinstance(body, dict):
            raise ChatGPTAuthError("ChatGPT identity keys are unavailable")
        return body

    def _validate_id_token(
        self,
        id_token: str,
        jwks: Mapping[str, Any],
        *,
        issued_client_id: str,
        expected_nonce: str,
        current_time: float | None = None,
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
            key_data = next(
                item
                for item in jwks.get("keys", [])
                if isinstance(item, dict) and item.get("kid") == header.get("kid")
            )
            signing_key = jwt.PyJWK.from_dict(key_data).key
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                issuer=self.config.issuer,
                audience=issued_client_id,
                options={"verify_exp": False, "verify_iat": False},
            )
        except (StopIteration, jwt.PyJWTError, ValueError, TypeError) as exc:
            raise ChatGPTAuthError("ChatGPT identity token validation failed") from exc
        now = self._clock() if current_time is None else current_time
        try:
            expiry = float(claims["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT identity token has invalid expiry") from exc
        if expiry <= now:
            raise ChatGPTAuthError("ChatGPT identity token has expired")
        if not secrets.compare_digest(str(claims.get("nonce", "")), expected_nonce):
            raise ChatGPTAuthError("ChatGPT identity token nonce mismatch")
        if not str(claims.get("sub", "")).strip() or not str(claims.get("email", "")).strip():
            raise ChatGPTAuthError("ChatGPT identity token lacks account identity")
        return dict(claims)

    async def _list_models_with_token(self, access_token: str) -> list[ChatGPTModel]:
        session = self._get_session()
        try:
            async with session.get(
                f"{self.config.api_base_url}/models",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT model catalog is unavailable") from exc
        if response.status >= 400 or not isinstance(body, dict):
            raise ChatGPTAuthError("ChatGPT model catalog is unavailable")
        models = []
        for item in body.get("models", []):
            if not isinstance(item, dict) or item.get("visibility") != "list":
                continue
            slug = str(item.get("slug", "") or "").strip()
            if slug:
                models.append(
                    ChatGPTModel(
                        slug=slug,
                        display_name=str(item.get("display_name", slug) or slug),
                    )
                )
        if not models:
            raise ChatGPTAuthError("ChatGPT account has no visible models")
        return models

    async def get_access_token(self) -> str:
        credentials = self._credentials
        if credentials is None:
            raise ChatGPTAuthError("ChatGPT is not signed in; run chatgpt-login")
        if not self._force_refresh and self._access_token_valid(
            credentials, self.config.refresh_leeway
        ):
            return credentials.access_token

        async with self._refresh_lock:
            credentials = self._credentials
            if credentials is None:
                raise ChatGPTAuthError("ChatGPT is not signed in; run chatgpt-login")
            if not self._force_refresh and self._access_token_valid(
                credentials, self.config.refresh_leeway
            ):
                return credentials.access_token
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(
                    self._refresh_and_persist(credentials)
                )
            task = self._refresh_task

        try:
            refreshed = await asyncio.shield(task)
            return refreshed.access_token
        except ChatGPTAuthError as exc:
            terminal_codes = {
                "invalid_grant",
                "invalid_refresh_token",
                "token_expired",
                "refresh_token_expired",
                "refresh_token_invalidated",
                "refresh_token_reused",
                "invalid_client",
            }
            current = self._credentials
            if (
                exc.error_code not in terminal_codes
                and current is not None
                and self._access_token_valid(current, 0)
            ):
                return current.access_token
            raise
        finally:
            if task.done():
                async with self._refresh_lock:
                    if self._refresh_task is task:
                        self._refresh_task = None

    async def _refresh_and_persist(
        self, credentials: ChatGPTCredentials
    ) -> ChatGPTCredentials:
        refreshed = await self._refresh_credentials(credentials)
        self.store.save(refreshed)
        self._credentials = refreshed
        self._force_refresh = False
        return refreshed

    async def _refresh_credentials(
        self, credentials: ChatGPTCredentials
    ) -> ChatGPTCredentials:
        session = self._get_session()
        data = {
            "grant_type": "refresh_token",
            "client_id": credentials.issued_client_id,
            "refresh_token": credentials.refresh_token,
            "resource": self.config.resource,
        }
        try:
            async with session.post(self.config.token_endpoint, data=data) as response:
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT token refresh is temporarily unavailable") from exc
        if not isinstance(body, dict):
            raise ChatGPTAuthError("ChatGPT token refresh returned invalid data")
        if response.status >= 400:
            error_code = str(body.get("error", "refresh_failed") or "refresh_failed")
            terminal_codes = {
                "invalid_grant",
                "invalid_refresh_token",
                "token_expired",
                "refresh_token_expired",
                "refresh_token_invalidated",
                "refresh_token_reused",
                "invalid_client",
            }
            message = (
                "ChatGPT session is no longer renewable; sign in again"
                if error_code in terminal_codes
                else "ChatGPT token refresh was rejected"
            )
            raise ChatGPTAuthError(message, error_code=error_code)

        access_token = str(body.get("access_token", "") or "").strip()
        refresh_token = str(body.get("refresh_token", "") or "").strip()
        if not access_token or not refresh_token:
            raise ChatGPTAuthError("ChatGPT token refresh omitted rotating credentials")
        raw_scope = body.get("scope")
        scopes = (
            tuple(str(raw_scope).split())
            if raw_scope is not None
            else credentials.scopes
        )
        if "chatgpt.tokens.use.direct" not in scopes:
            raise ChatGPTAuthError("ChatGPT token refresh lacks direct usage scope")
        try:
            expires_in = int(body.get("expires_in", 0))
        except (TypeError, ValueError) as exc:
            raise ChatGPTAuthError("ChatGPT token refresh has invalid expiry") from exc
        if expires_in <= 0:
            raise ChatGPTAuthError("ChatGPT token refresh has invalid expiry")
        return ChatGPTCredentials.from_mapping(
            {
                **credentials.to_mapping(),
                "id_token": str(body.get("id_token") or credentials.id_token),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": str(body.get("token_type") or credentials.token_type),
                "expires_at": self._clock() + expires_in,
                "scopes": scopes,
            }
        )

    def invalidate_access_token(self) -> None:
        self._force_refresh = True

    async def list_models(self) -> list[ChatGPTModel]:
        return await self._list_models_with_token(await self.get_access_token())

    async def require_visible_model(self, configured_model: str = "") -> str:
        credentials = self._credentials
        target = str(configured_model or "").strip()
        if not target and credentials is not None:
            target = credentials.selected_model
        visible = {model.slug for model in await self.list_models()}
        if not target or target not in visible:
            raise ChatGPTAuthError("Configured ChatGPT model is not available")
        return target

    async def logout(self) -> LogoutResult:
        credentials = self._credentials
        confirmed = False
        if credentials is not None:
            try:
                discovery_url = f"{self.config.issuer}/.well-known/openid-configuration"
                session = self._get_session()
                async with session.get(discovery_url) as response:
                    metadata = await response.json(content_type=None)
                revocation_endpoint = (
                    str(metadata.get("revocation_endpoint", "") or "").strip()
                    if response.status < 400 and isinstance(metadata, dict)
                    else ""
                )
                if not revocation_endpoint:
                    raise ChatGPTAuthError("ChatGPT revocation endpoint is unavailable")
                async with session.post(
                    revocation_endpoint,
                    data={
                        "token": credentials.refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": credentials.issued_client_id,
                    },
                ) as response:
                    confirmed = response.status == 200
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                ValueError,
                ChatGPTAuthError,
            ):
                confirmed = False
        self.store.delete()
        self._credentials = None
        self._force_refresh = False
        return LogoutResult(
            revocation_confirmed=confirmed,
            local_credentials_deleted=True,
        )

    def _access_token_valid(
        self, credentials: ChatGPTCredentials, leeway: int
    ) -> bool:
        return credentials.expires_at > self._clock() + leeway

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or getattr(self._session, "closed", False):
            self._session = self._session_factory()
        return self._session

    async def close(self) -> None:
        refresh_task = self._refresh_task
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task
        self._refresh_task = None
        if self._callback_runner is not None:
            await self._callback_runner.cleanup()
            self._callback_runner = None
        if self._session is not None and not getattr(self._session, "closed", False):
            await self._session.close()
        self._session = None
