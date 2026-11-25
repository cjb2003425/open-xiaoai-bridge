"""OAuth 2.0 Device Authorization support for OpenAI-compatible backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

from core.utils.base import get_env


class OAuthDeviceConfigError(ValueError):
    """Raised when Device OAuth configuration is incomplete or invalid."""


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
