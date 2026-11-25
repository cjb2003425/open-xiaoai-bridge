import asyncio
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp

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
        if isinstance(self._body, BaseException):
            raise self._body
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

    def write_token(self, token):
        path = Path(self.config.token_file)
        path.write_text(json.dumps(token), encoding="utf-8")
        return path

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

    async def test_invalid_json_response_raises_safe_error(self):
        manager, _session = self.manager(
            [(200, json.JSONDecodeError("bad", "not-json", 0))]
        )

        with self.assertRaisesRegex(
            OAuthDeviceAuthorizationError, "returned invalid JSON"
        ):
            await manager.get_access_token()

    async def test_valid_persisted_token_skips_network(self):
        self.write_token(
            {
                "access_token": "persisted-access",
                "refresh_token": "persisted-refresh",
                "expires_at": 2_000.0,
            }
        )
        manager, session = self.manager([])

        self.assertEqual("persisted-access", await manager.get_access_token())
        self.assertEqual([], session.calls)

    async def test_token_file_is_atomically_written_with_mode_0600(self):
        manager, _ = self.manager(
            [
                (200, self.device_response()),
                (200, self.token_response()),
            ]
        )

        await manager.get_access_token()

        path = Path(self.config.token_file)
        self.assertTrue(path.exists())
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual([], list(path.parent.glob(f".{path.name}.*.tmp")))

    async def test_corrupt_token_file_is_moved_to_invalid_suffix(self):
        path = Path(self.config.token_file)
        path.write_text("not-json", encoding="utf-8")
        manager, _ = self.manager(
            [
                (200, self.device_response()),
                (200, self.token_response()),
            ]
        )

        self.assertEqual("access-1", await manager.get_access_token())
        self.assertTrue(path.with_suffix(path.suffix + ".invalid").exists())

    async def test_refresh_happens_within_leeway(self):
        self.write_token(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": 1_100.0,
            }
        )
        manager, session = self.manager([(200, self.token_response())])

        self.assertEqual("access-1", await manager.get_access_token())
        self.assertEqual("refresh_token", session.calls[0][1]["grant_type"])
        self.assertEqual("old-refresh", session.calls[0][1]["refresh_token"])

    async def test_refresh_without_new_refresh_token_retains_old_value(self):
        self.write_token(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": 1_100.0,
            }
        )
        response = self.token_response(access_token="new-access")
        response.pop("refresh_token")
        manager, _ = self.manager([(200, response)])

        self.assertEqual("new-access", await manager.get_access_token())
        stored = json.loads(Path(self.config.token_file).read_text(encoding="utf-8"))
        self.assertEqual("old-refresh", stored["refresh_token"])

    async def test_rotating_refresh_token_replaces_old_value(self):
        self.write_token(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": 1_100.0,
            }
        )
        response = self.token_response(access_token="new-access")
        response["refresh_token"] = "rotated-refresh"
        manager, _ = self.manager([(200, response)])

        await manager.get_access_token()
        stored = json.loads(Path(self.config.token_file).read_text(encoding="utf-8"))
        self.assertEqual("rotated-refresh", stored["refresh_token"])

    async def test_refresh_outage_uses_still_valid_access_token(self):
        self.write_token(
            {
                "access_token": "still-valid",
                "refresh_token": "old-refresh",
                "expires_at": 1_100.0,
            }
        )
        manager, _ = self.manager([aiohttp.ClientConnectionError("offline")])

        self.assertEqual("still-valid", await manager.get_access_token())

    async def test_invalid_refresh_token_reauthorizes(self):
        self.write_token(
            {
                "access_token": "expired",
                "refresh_token": "invalid-refresh",
                "expires_at": 900.0,
            }
        )
        manager, _ = self.manager(
            [
                (400, {"error": "invalid_grant"}),
                (200, self.device_response()),
                (200, self.token_response(access_token="reauthorized")),
            ]
        )

        self.assertEqual("reauthorized", await manager.get_access_token())

    async def test_concurrent_callers_share_one_refresh(self):
        manager, _ = self.manager([])
        manager._token = {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 1_100.0,
        }
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()

        async def refresh_once():
            refresh_started.set()
            await release_refresh.wait()
            return {
                "access_token": "shared-access",
                "refresh_token": "shared-refresh",
                "expires_at": 2_000.0,
            }

        manager._refresh_access_token = AsyncMock(side_effect=refresh_once)
        first = asyncio.create_task(manager.get_access_token())
        await refresh_started.wait()
        second = asyncio.create_task(manager.get_access_token())
        await asyncio.sleep(0)
        release_refresh.set()

        self.assertEqual(
            ["shared-access", "shared-access"],
            await asyncio.gather(first, second),
        )
        manager._refresh_access_token.assert_awaited_once()

    async def test_close_cancels_poll_unblocks_waiters_and_closes_session(self):
        polling_started = asyncio.Event()

        async def blocking_sleep(_delay):
            polling_started.set()
            await asyncio.Event().wait()

        session = FakeSession([(200, self.device_response())])
        manager = DeviceOAuthTokenManager(
            self.config,
            session_factory=lambda: session,
            sleep=blocking_sleep,
            clock=lambda: 1_000.0,
        )
        first = asyncio.create_task(manager.get_access_token())
        second = asyncio.create_task(manager.get_access_token())
        await polling_started.wait()

        await manager.close()
        results = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True),
            timeout=0.2,
        )

        self.assertTrue(all(isinstance(item, asyncio.CancelledError) for item in results))
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
