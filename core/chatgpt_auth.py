"""Official Sign in with ChatGPT authentication primitives.

This module deliberately keeps ChatGPT credentials separate from the
OpenAI-compatible API-key backend.  The protocol implementation is layered on
top of these validated, redacted, and atomically persisted data types.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


class ChatGPTAuthError(RuntimeError):
    """A safe-to-display SIWC configuration or authentication error."""


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
