# sherpa-onnx 2.0.2 Upgrade and Qwen3-ASR Evaluation Design

**Date:** 2026-11-21

## Goal

Upgrade the existing non-Docker `open-xiaoai-bridge` installation from
`sherpa-onnx` 1.12.28 to 2.0.2, preserve the working native XiaoAI KWS,
VAD, SenseVoice, and DeepSeek path, then evaluate Qwen3-ASR 0.6B INT8 as a
temporary live ASR backend. Qwen3-ASR may enter the live speaker test only
when one utterance completes within three seconds in the offline gate.

The active production fallback remains
`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`.

## Constraints

- Do not use Docker.
- Keep native XiaoAI KWS on the speaker; do not enable server-side KWS.
- Do not reconnect Hermes or restart `xiaozhi-esp32-server`.
- Keep the existing DeepSeek conversation path unchanged.
- Preserve the user's current VAD and local configuration changes.
- Perform the requested dependency upgrade in the existing project virtual
  environment, not in a shadow environment.
- Do not promote Qwen3-ASR merely because it loads. Promotion requires the
  latency, recognition, and live stability gates below.

## Upgrade Architecture

Before changing the environment, capture the current Python, `uv`,
`sherpa-onnx`, `onnxruntime`, lockfile, service status, and active ASR model.
Ensure that the currently installed 1.12.28 artifact remains recoverable from
the package cache or download it before stopping the service.

Pin `sherpa-onnx` to 2.0.2 in the project dependency metadata, regenerate the
lockfile mechanically with `uv`, and synchronize the existing virtual
environment. The service is stopped only for the synchronization and smoke
test window. The first post-upgrade test uses the current SenseVoice 2024
backend so the dependency upgrade is separated from the Qwen integration.

The 2.0.2 upgrade is accepted only if imports succeed, the existing
SenseVoice recognizer can be constructed and used, focused tests pass, and
the service returns to a healthy state with the speaker path working. If any
of these checks fails, restore the prior dependency metadata and lockfile,
reinstall 1.12.28, restart the service, and verify SenseVoice 2024 again.

## Qwen3-ASR Backend

Use the official sherpa-onnx Qwen3-ASR 0.6B INT8 archive. Validate the
downloaded archive before extraction, including its expected byte length
(`878702423`), archive readability, and required model members. The installed
model must contain the convolution frontend, INT8 encoder, INT8 decoder, and
tokenizer directory expected by sherpa-onnx.

Extend the existing sherpa ASR adapter with a distinct `qwen3_asr` backend.
It will build an offline recognizer using the Qwen3-ASR-specific model
configuration rather than pretending Qwen is a SenseVoice or Paraformer
layout. Model validation must support required files and required directories
without weakening validation for the existing backends.

Qwen defaults will be conservative and deterministic: maximum total length
512, maximum 128 new tokens, temperature `1e-6`, top-p `0.8`, and seed 42,
unless the installed 2.0.2 API requires an equivalent spelling. Any API
difference is resolved against the installed package interface and covered by
tests; no guessed compatibility shim is allowed.

## Evaluation Gates

First run offline recognition against the same captured PCM input used for
the current SenseVoice comparison. Record model load time, recognition time
for repeated short inputs, peak or resident memory where available, raw
transcripts, and failures. Compare Qwen3-ASR with the current SenseVoice 2024
backend on identical audio.

Qwen3-ASR is eligible for temporary live testing only when:

1. recognizer construction and repeated decoding are stable;
2. each representative utterance completes in no more than three seconds;
3. memory use does not destabilize the host or the running service; and
4. the raw transcript is usable enough to justify a speaker trial.

For the live trial, temporarily select `qwen3_asr`, restart the bridge, and
collect raw ASR output for three repetitions each of:

- `大西洋有多大`
- `大象有多大`

The live trial also checks that native wake, recording handoff, VAD endpoint,
DeepSeek request, and speaker response still form one working path. Qwen is
not made the lasting default until the user accepts its observed accuracy and
latency.

## Failure Handling and Rollback

There are two independent rollback boundaries:

1. **Dependency rollback:** if sherpa-onnx 2.0.2 breaks existing SenseVoice
   behavior or service stability, restore 1.12.28 plus the previous lock and
   dependency metadata.
2. **Model rollback:** if Qwen fails its offline gate, exceeds three seconds,
   consumes unsafe resources, or fails the live trial, restore the active ASR
   model to SenseVoice 2024 while retaining 2.0.2 only if its compatibility
   gate already passed.

Every service-affecting failure ends with an explicit service restart and a
health/log check. The existing user-edited configuration is backed up and
changed only at the ASR selection fields required for the temporary trial.

## Verification

Automated verification covers:

- current sherpa backend regression tests under 2.0.2;
- Qwen3-ASR model-path validation, including tokenizer-directory handling;
- Qwen configuration construction using the installed API;
- recognizer factory selection for existing backends and `qwen3_asr`;
- failure messages for incomplete or invalid Qwen model directories; and
- configuration parsing without changing KWS, VAD, or DeepSeek settings.

Operational verification covers:

- service active state and clean startup logs;
- SenseVoice 2024 recognition immediately after the dependency upgrade;
- offline Qwen latency, transcripts, and memory observations;
- the two live Chinese contrast phrases when the offline gate passes; and
- confirmed restoration of SenseVoice 2024 after a rejected trial.

## Deliverables

- dependency metadata and lockfile updated to sherpa-onnx 2.0.2;
- Qwen3-ASR backend support and focused tests;
- a locally installed, verified Qwen3-ASR 0.6B INT8 model;
- recorded benchmark and live-test observations; and
- either a user-accepted Qwen active configuration or a verified SenseVoice
  2024 fallback, with no ambiguous intermediate state.
