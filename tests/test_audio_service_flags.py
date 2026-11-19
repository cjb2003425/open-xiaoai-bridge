import os
import unittest
from unittest.mock import patch

from core.utils.env import audio_service_flags


class AudioServiceFlagsTest(unittest.TestCase):
    def test_defaults_enable_vad_and_kws(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual((True, True), audio_service_flags())

    def test_kws_can_be_disabled_without_disabling_vad(self):
        with patch.dict(
            os.environ,
            {"AUDIO_INPUT_ENABLE": "1", "KWS_ENABLE": "0"},
            clear=True,
        ):
            self.assertEqual((True, False), audio_service_flags())

    def test_disabling_audio_disables_both_services(self):
        with patch.dict(
            os.environ,
            {"AUDIO_INPUT_ENABLE": "false", "KWS_ENABLE": "true"},
            clear=True,
        ):
            self.assertEqual((False, False), audio_service_flags())


if __name__ == "__main__":
    unittest.main()
