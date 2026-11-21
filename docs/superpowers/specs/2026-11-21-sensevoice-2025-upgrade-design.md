# SenseVoice 2025 Upgrade Design

## Goal

Correct the raw local-ASR transcript for `大西洋有多大` without turning genuine `大象有多大` queries into `大西洋`, while keeping local inference below one second.

## Approach

Evaluate the official `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09` model as a side-by-side candidate for the active `2024-07-17` SenseVoice model. Do not overwrite or remove the active model. Promote the candidate only after offline performance checks and live contrast testing pass.

This upgrade does not add text replacements, forced hotwords, homophone rules, a second cloud request, or a second recognizer in the live path. It leaves native XiaoAI KWS, recorder handoff, VAD, DeepSeek, TTS, and conversation behavior unchanged.

## Installation and Offline Evaluation

1. Download the official int8 model into a new directory under `core/models/`.
2. Verify the archive and required `model.int8.onnx` and `tokens.txt` files before loading it.
3. Load the candidate independently of the running service.
4. Benchmark old and new models with the same Chinese audio inputs at approximately 3, 5, and 10 seconds.
5. Record inference time and peak process memory. Candidate inference must remain below one second and must not be materially slower than the active model.

The active configuration remains unchanged during download and offline evaluation.

## Promotion

After offline checks pass, change only `asr.model_dir` in `/home/torrey/.config/open-xiaoai-bridge/config.py` to the 2025 model directory and restart the user service. Confirm from fresh startup logs that:

- `open-xiaoai-bridge.service` is active;
- SenseVoice loads from the `2025-09-09` directory with int8 enabled;
- VAD remains at 250 ms minimum speech, 1200 ms minimum silence, and threshold 0.1;
- the speaker reconnects at `10.0.0.5`;
- server KWS remains disabled and native XiaoAI KWS remains the wake source.

Do not change the repository default model until the live quality gate passes.

## Live Quality Gate

Run six independent native-wake turns and inspect the raw ASR transcript in service logs:

- say `大西洋有多大` three times; all three must retain `大西洋`;
- say `大象有多大` three times; all three must retain `大象`;
- each turn must produce one native wake and one answer path;
- the candidate's same-audio offline benchmark must remain below one second.

DeepSeek's answer is not evidence for this gate; only the transcript produced by local ASR counts.

## Rollback and Failure Handling

If model loading, latency, memory, service startup, or any live transcript fails its gate:

1. restore `asr.model_dir` to `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`;
2. restart the user service;
3. verify the old SenseVoice model, 1200 ms VAD endpoint, and `10.0.0.5` connection in fresh logs;
4. stop this approach without adding a replacement rule or another correction layer.

The downloaded candidate may remain installed for analysis. No speaker-side rollback is required.

## Scope

- Add the official candidate model as a separate local asset.
- Benchmark it against the active model.
- Temporarily promote it through the active configuration for live validation.
- Update the repository default `asr.model_dir` only after every quality gate passes.
- Preserve all unrelated working-tree changes and existing model directories.
