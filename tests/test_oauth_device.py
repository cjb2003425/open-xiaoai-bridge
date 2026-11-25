import asyncio
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.oauth_device import (
    DeviceOAuthTokenManager,
    OAuthDeviceAuthorizationError,
    OAuthDeviceConfig,
    OAuthDeviceConfigError,
)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body


class FakeRequestContext:
    def __init__(self, result):
        self._result = result

    async def __aenter__(self):
        if isinstance(self._result, BaseException):
            raise self._result
        status, body = self._result
        return FakeResponse(status, body)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, *, data):
        self.calls.append((url, dict(data)))
        if not self.responses:
            raise AssertionError(f"unexpected POST {url}")
        return FakeRequestContext(self.responses.pop(0))

    async def close(self):
        self.closed = True


class OAuthDeviceConfigTest(unittest.TestCase):
    def test_config_parses_required_and_optional_fields(self):
        config = OAuthDeviceConfig.from_mapping(
            {
                "device_authorization_endpoint": "https://auth.example.com/device",
                "token_endpoint": "https://auth.example.com/token",
                "client_id": " bridge-client ",
                "scope": " openid offline_access api ",
                "audience": " bridge-api ",
            }
        )

        self.assertEqual(
            "https://auth.example.com/device",
            config.device_authorization_endpoint,
        )
        self.assertEqual("https://auth.example.com/token", config.token_endpoint)
        self.assertEqual("bridge-client", config.client_id)
        self.assertEqual("openid offline_access api", config.scope)
        self.assertEqual("bridge-api", config.audience)
        self.assertEqual("./data/openai-oauth-token.json", str(config.token_file))
        self.assertEqual(120, config.refresh_leeway)
        self.assertEqual("", config.client_secret)

    def test_config_reads_client_secret_from_environment(self):
        with patch(
            "core.oauth_device.get_env",
            side_effect=lambda name: (
                "runtime-secret" if name == "OPENAI_OAUTH_CLIENT_SECRET" else None
            ),
        ):
            config = OAuthDeviceConfig.from_mapping(
                {
                    "device_authorization_endpoint": "https://auth.example.com/device",
                    "token_endpoint": "https://auth.example.com/token",
                    "client_id": "bridge-client",
                }
            )

        self.assertEqual("runtime-secret", config.client_secret)
        self.assertNotIn("runtime-secret", repr(config))

    def test_config_rejects_missing_endpoint_or_client_id(self):
        base = {
            "device_authorization_endpoint": "https://auth.example.com/device",
            "token_endpoint": "https://auth.example.com/token",
            "client_id": "bridge-client",
        }
        for missing in base:
            with self.subTest(missing=missing):
                invalid = dict(base)
                invalid[missing] = ""
                with self.assertRaisesRegex(OAuthDeviceConfigError, missing):
                    OAuthDeviceConfig.from_mapping(invalid)

        with self.assertRaisesRegex(OAuthDeviceConfigError, "absolute HTTP"):
            OAuthDeviceConfig.from_mapping(
                {
                    **base,
                    "token_endpoint": "/oauth/token",
                }
            )


class DeviceOAuthProtocolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = OAuthDeviceConfig.from_mapping(
            {
                "device_authorization_endpoint": "https://auth.example.com/device",
                "token_endpoint": "https://auth.example.com/token",
                "client_id": "bridge-client",
                "scope": "openid offline_access api",
                "token_file": str(Path(self.temp_dir.name) / "token.json"),
            }
        )
        self.sleep_calls = []

    async def fake_sleep(self, delay):
        self.sleep_calls.append(delay)

    def manager(self, responses, *, config=None):
        session = FakeSession(responses)
        manager = DeviceOAuthTokenManager(
            config or self.config,
            session_factory=lambda: session,
            sleep=self.fake_sleep,
            clock=lambda: 1_000.0,
        )
        return manager, session

    @staticmethod
    def device_response(*, code="device-1", interval=1):
        return {
            "device_code": code,
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example.com/activate",
            "expires_in": 600,
            "interval": interval,
        }

    @staticmethod
    def token_response(*, access_token="access-1"):
        return {
            "access_token": access_token,
            "refresh_token": "refresh-1",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    async def test_device_flow_logs_code_and_polls_until_success(self):
        manager, session = self.manager(
            [
                (200, self.device_response()),
                (200, self.token_response()),
            ]
        )

        with patch("core.oauth_device.logger.info") as log_info:
            token = await manager.get_access_token()

        self.assertEqual("access-1", token)
        rendered_logs = " ".join(
            " ".join(str(part) for part in call.args)
            for call in log_info.call_args_list
        )
        self.assertIn("https://auth.example.com/activate", rendered_logs)
        self.assertIn("ABCD-EFGH", rendered_logs)
        self.assertNotIn("access-1", rendered_logs)
        self.assertEqual(self.config.token_endpoint, session.calls[-1][0])
        self.assertEqual(
            "urn:ietf:params:oauth:grant-type:device_code",
            session.calls[-1][1]["grant_type"],
        )

    async def test_authorization_pending_uses_server_interval(self):
        manager, _ = self.manager(
            [
                (200, self.device_response(interval=3)),
                (400, {"error": "authorization_pending"}),
                (200, self.token_response()),
            ]
        )

        self.assertEqual("access-1", await manager.get_access_token())
        self.assertEqual([3, 3], self.sleep_calls)

    async def test_slow_down_adds_five_seconds_to_interval(self):
        manager, _ = self.manager(
            [
                (200, self.device_response(interval=2)),
                (400, {"error": "slow_down"}),
                (200, self.token_response()),
            ]
        )

        self.assertEqual("access-1", await manager.get_access_token())
        self.assertEqual([2, 7], self.sleep_calls)

    async def test_access_denied_fails_without_retry(self):
        manager, session = self.manager(
            [
                (200, self.device_response()),
                (400, {"error": "access_denied"}),
            ]
        )

        with self.assertRaisesRegex(OAuthDeviceAuthorizationError, "denied"):
            await manager.get_access_token()
        self.assertEqual(2, len(session.calls))

    async def test_expired_device_code_starts_one_new_authorization(self):
        manager, session = self.manager(
            [
                (200, self.device_response(code="expired-device")),
                (400, {"error": "expired_token"}),
                (200, self.device_response(code="fresh-device")),
                (200, self.token_response()),
            ]
        )

        self.assertEqual("access-1", await manager.get_access_token())
        device_calls = [
            call
            for call in session.calls
            if call[0] == self.config.device_authorization_endpoint
        ]
        self.assertEqual(2, len(device_calls))

    async def test_empty_audience_and_secret_are_omitted(self):
        manager, session = self.manager(
            [
                (200, self.device_response()),
                (200, self.token_response()),
            ]
        )

        await manager.get_access_token()

        for _url, data in session.calls:
            self.assertNotIn("audience", data)
            self.assertNotIn("client_secret", data)

    async def test_invalid_token_response_does_not_replace_current_token(self):
        manager, _ = self.manager(
            [
                (200, self.device_response()),
                (200, {"access_token": "new-without-expiry"}),
            ]
        )
        manager._token = {
            "access_token": "old-token",
            "expires_at": 900.0,
        }

        with self.assertRaisesRegex(OAuthDeviceAuthorizationError, "expires_in"):
            await manager.get_access_token()
        self.assertEqual("old-token", manager._token["access_token"])


if __name__ == "__main__":
    unittest.main()
