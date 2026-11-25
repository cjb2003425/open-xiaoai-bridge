import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.oauth_device import OAuthDeviceConfig, OAuthDeviceConfigError


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


if __name__ == "__main__":
    unittest.main()
