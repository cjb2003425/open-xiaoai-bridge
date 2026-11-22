# Paraformer Large Promotion Design

## Goal

Promote the installed Paraformer Large INT8 model to both the active
Open-XiaoAI Bridge configuration and the repository default after its
same-audio benchmark showed correct recognition of `大西洋有多大` and the
lowest measured average latency among the evaluated local ASR models.

## Selected Model

- Backend: `paraformer`
- Model directory: `sherpa-onnx-paraformer-zh-2024-03-09`
- Quantization: INT8
- Runtime: sherpa-onnx 2.0.2 with CPU provider

The trilingual Paraformer directory must not be selected implicitly. Both the
active and repository configuration will name the exact Large model directory.

## Configuration Changes

Update only the ASR selection fields in these files:

1. `/home/torrey/.config/open-xiaoai-bridge/config.py`
2. `/home/torrey/workspace/open-xiaoai-bridge/config.py`

The resulting ASR selection in both locations will be:

```python
"model": "paraformer",
"int8": True,
"model_dir": "sherpa-onnx-paraformer-zh-2024-03-09",
```

The repository `config.py` already contains unrelated user changes, including
the active VAD pause-tolerance values. Those changes must remain byte-for-byte
outside the narrow ASR selection hunk and must not be staged accidentally.

## Runtime Invariants

The promotion must preserve:

- native XiaoAI KWS on the speaker;
- disabled Bridge/server-side KWS;
- VAD minimum speech 250 ms;
- VAD minimum silence 1200 ms;
- VAD threshold 0.1;
- direct DeepSeek routing without Hermes or xiaozhi-server;
- speaker address `10.0.0.5`; and
- disabled `xiaozhi-esp32-server` startup.

No TTS, wake-word, VAD, DeepSeek, or client changes are in scope.

## Execution and Verification

Before editing, record the current active ASR fields, service state, restart
count, and a recoverable copy of the active configuration. Apply narrow patches
to the two ASR sections, then restart the user service.

Fresh service logs must confirm:

- backend `paraformer`;
- exact Large model directory;
- INT8 enabled;
- VAD `250/1200/0.1`;
- KWS disabled in the Bridge process; and
- speaker `10.0.0.5` connected.

Run focused ASR tests and the full automated test suite. Inspect the repository
diff to ensure that only the intended ASR hunk is attributed to this promotion.

After startup verification, perform a native-wake live utterance of
`大西洋有多大` and inspect the raw ASR transcript. The DeepSeek response is not
evidence of ASR correctness; the local transcript itself must contain the
intended phrase.

## Rollback

If the service fails to start, the Paraformer model fails to load, the speaker
does not reconnect, or a runtime invariant changes, restore only these active
ASR fields:

```python
"model": "sense_voice",
"int8": True,
"model_dir": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
```

Restart the service and require the same health checks. Do not roll back or
overwrite unrelated configuration.

If the service is healthy but the live phrase is wrong, retain the benchmark
evidence, restore SenseVoice 2024 in the active configuration, and leave the
repository promotion uncommitted pending user direction.

## Acceptance Criteria

The promotion is complete when:

1. active and repository ASR fields explicitly select Paraformer Large INT8;
2. focused and full tests pass;
3. the restarted service is active with zero unexpected restarts;
4. startup logs prove the exact model, VAD, KWS, and speaker invariants;
5. the raw live transcript recognizes `大西洋有多大`; and
6. the final commit contains only the intended repository ASR selection hunk
   and any separately approved documentation.
