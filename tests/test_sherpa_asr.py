import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.services.audio.asr.sherpa import _SherpaASR


def _config(*, model: str, model_dir: str, int8: bool = True) -> mock.Mock:
    values = {
        "asr.model": model,
        "asr.model_dir": model_dir,
        "asr.int8": int8,
    }
    config = mock.Mock()
    config.get_app_config.side_effect = (
        lambda path, default=None: values.get(path, default)
    )
    return config


def _write_qwen_files(model_dir: Path, *, int8: bool = True) -> None:
    (model_dir / "conv_frontend.onnx").write_bytes(b"conv")
    suffix = ".int8.onnx" if int8 else ".onnx"
    (model_dir / f"encoder{suffix}").write_bytes(b"encoder")
    (model_dir / f"decoder{suffix}").write_bytes(b"decoder")


def _write_tokenizer(tokenizer_dir: Path) -> None:
    tokenizer_dir.mkdir()
    (tokenizer_dir / "vocab.json").write_text("{}", encoding="utf-8")
    (tokenizer_dir / "merges.txt").write_text("#version: 0.2", encoding="utf-8")
    (tokenizer_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")


class SherpaASRModelLayoutTest(unittest.TestCase):
    def test_qwen_backend_selects_expected_model_paths(self):
        cases = [
            (True, "encoder.int8.onnx", "decoder.int8.onnx"),
            (False, "encoder.onnx", "decoder.onnx"),
        ]

        for int8, expected_encoder, expected_decoder in cases:
            with self.subTest(int8=int8):
                asr = _SherpaASR()
                config = _config(model="qwen3_asr", model_dir="qwen", int8=int8)
                with mock.patch(
                    "core.services.audio.asr.sherpa.ConfigManager.instance",
                    return_value=config,
                ):
                    self.assertEqual("qwen3_asr", asr._get_backend())
                    self.assertEqual(
                        {
                            "conv_frontend": "conv_frontend.onnx",
                            "encoder": expected_encoder,
                            "decoder": expected_decoder,
                        },
                        asr._get_required_model_files("qwen3_asr"),
                    )
                    self.assertEqual(
                        {"tokenizer": "tokenizer"},
                        asr._get_required_model_dirs("qwen3_asr"),
                    )

    def test_qwen_backend_rejects_regular_file_named_tokenizer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_root = Path(temp_dir)
            model_dir = models_root / "qwen-model"
            model_dir.mkdir()
            _write_qwen_files(model_dir)
            (model_dir / "tokenizer").write_text(
                "not a directory", encoding="utf-8"
            )
            asr = _SherpaASR()
            config = _config(model="qwen3_asr", model_dir=model_dir.name)

            with (
                mock.patch(
                    "core.services.audio.asr.sherpa.ConfigManager.instance",
                    return_value=config,
                ),
                mock.patch(
                    "core.services.audio.asr.sherpa.get_model_file_path",
                    return_value=str(models_root),
                ),
                self.assertRaisesRegex(FileNotFoundError, "tokenizer"),
            ):
                asr._find_model_dir(
                    "qwen3-asr",
                    asr._get_required_model_files("qwen3_asr"),
                    asr._get_required_model_dirs("qwen3_asr"),
                )

    def test_qwen_backend_rejects_incomplete_tokenizer(self):
        for missing_name in ("vocab.json", "merges.txt", "tokenizer_config.json"):
            with self.subTest(missing_name=missing_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    models_root = Path(temp_dir)
                    model_dir = models_root / "qwen-model"
                    model_dir.mkdir()
                    _write_qwen_files(model_dir)
                    _write_tokenizer(model_dir / "tokenizer")
                    (model_dir / "tokenizer" / missing_name).unlink()
                    asr = _SherpaASR()
                    config = _config(model="qwen3_asr", model_dir=model_dir.name)

                    with (
                        mock.patch(
                            "core.services.audio.asr.sherpa.ConfigManager.instance",
                            return_value=config,
                        ),
                        mock.patch(
                            "core.services.audio.asr.sherpa.get_model_file_path",
                            return_value=str(models_root),
                        ),
                        self.assertRaisesRegex(FileNotFoundError, missing_name),
                    ):
                        asr._find_model_dir(
                            "qwen3-asr",
                            asr._get_required_model_files("qwen3_asr"),
                            asr._get_required_model_dirs("qwen3_asr"),
                        )

    def test_sense_voice_model_file_validation_still_works(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_root = Path(temp_dir)
            model_dir = models_root / "sense-voice-model"
            model_dir.mkdir()
            (model_dir / "model.int8.onnx").write_bytes(b"model")
            asr = _SherpaASR()
            config = _config(model="sense_voice", model_dir=model_dir.name)

            with (
                mock.patch(
                    "core.services.audio.asr.sherpa.ConfigManager.instance",
                    return_value=config,
                ),
                mock.patch(
                    "core.services.audio.asr.sherpa.get_model_file_path",
                    return_value=str(models_root),
                ),
            ):
                self.assertEqual(
                    str(model_dir),
                    asr._find_model_dir(
                        "sense-voice",
                        asr._get_required_model_files("sense_voice"),
                        asr._get_required_model_dirs("sense_voice"),
                    ),
                )


class SherpaASRFactoryTest(unittest.TestCase):
    def test_qwen_factory_receives_only_qwen_paths_and_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_root = Path(temp_dir)
            model_dir = models_root / "qwen-model"
            model_dir.mkdir()
            _write_qwen_files(model_dir)
            _write_tokenizer(model_dir / "tokenizer")
            asr = _SherpaASR()
            config = _config(model="qwen3_asr", model_dir=model_dir.name)

            with (
                mock.patch(
                    "core.services.audio.asr.sherpa.ConfigManager.instance",
                    return_value=config,
                ),
                mock.patch(
                    "core.services.audio.asr.sherpa.get_model_file_path",
                    return_value=str(models_root),
                ),
                mock.patch(
                    "core.services.audio.asr.sherpa.sherpa_onnx.OfflineRecognizer.from_qwen3_asr"
                ) as factory,
            ):
                asr._ensure_loaded()

            factory.assert_called_once_with(
                conv_frontend=str(model_dir / "conv_frontend.onnx"),
                encoder=str(model_dir / "encoder.int8.onnx"),
                decoder=str(model_dir / "decoder.int8.onnx"),
                tokenizer=str(model_dir / "tokenizer"),
                num_threads=2,
                debug=False,
                provider="cpu",
                max_total_len=512,
                max_new_tokens=128,
                temperature=1e-6,
                top_p=0.8,
                seed=42,
                hotwords="",
            )

    def test_sense_voice_factory_keeps_tokens_and_homophone_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_root = Path(temp_dir)
            model_dir = models_root / "sense-voice-model"
            model_dir.mkdir()
            (model_dir / "model.int8.onnx").write_bytes(b"model")
            (model_dir / "tokens.txt").write_text("tokens", encoding="utf-8")
            (models_root / "dict").mkdir()
            (models_root / "replace.fst").write_bytes(b"fst")
            (models_root / "lexicon.txt").write_text("lexicon", encoding="utf-8")
            asr = _SherpaASR()
            config = _config(model="sense_voice", model_dir=model_dir.name)

            with (
                mock.patch(
                    "core.services.audio.asr.sherpa.ConfigManager.instance",
                    return_value=config,
                ),
                mock.patch(
                    "core.services.audio.asr.sherpa.get_model_file_path",
                    return_value=str(models_root),
                ),
                mock.patch(
                    "core.services.audio.asr.sherpa.sherpa_onnx.OfflineRecognizer.from_sense_voice"
                ) as factory,
            ):
                asr._ensure_loaded()

            factory.assert_called_once_with(
                model=str(model_dir / "model.int8.onnx"),
                tokens=str(model_dir / "tokens.txt"),
                num_threads=2,
                debug=False,
                provider="cpu",
                language="auto",
                use_itn=True,
                hr_dict_dir=str(models_root / "dict"),
                hr_rule_fsts=str(models_root / "replace.fst"),
                hr_lexicon=str(models_root / "lexicon.txt"),
            )


if __name__ == "__main__":
    unittest.main()
