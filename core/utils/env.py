import os


def env_enabled(name: str, default: bool = True) -> bool:
    fallback = "true" if default else "false"
    return os.environ.get(name, fallback).strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def audio_service_flags() -> tuple[bool, bool]:
    """Return (VAD enabled, KWS enabled), keeping VAD for local ASR."""
    audio_input_enabled = env_enabled("AUDIO_INPUT_ENABLE")
    return audio_input_enabled, audio_input_enabled and env_enabled("KWS_ENABLE")
