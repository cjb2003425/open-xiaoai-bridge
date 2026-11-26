# VAD Pause Tolerance Design

## Goal

Allow a natural pause within one spoken request without ending recording after only 500 ms.

## Design

- Change `APP_CONFIG["vad"]["min_silence_duration"]` from `500` to `1200` milliseconds.
- Keep the speech probability threshold at `0.10` and the minimum speech duration at `250` milliseconds.
- Do not change KWS, ASR, DeepSeek routing, or maximum conversation timing.
- Reload the running user service only if configuration hot reload does not apply the new value.

## Expected behavior

A pause shorter than about 1.2 seconds remains part of the same utterance. A final silence longer than about 1.2 seconds ends the utterance, adding at most about 0.7 seconds of endpoint latency compared with the current setting.

## Verification

- Confirm the effective configuration contains `min_silence_duration: 1200` under `vad`.
- Confirm the bridge remains active and logs the new VAD silence duration.
- Speak a request containing a roughly one-second pause and verify it is transcribed as one request.
