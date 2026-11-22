# Paraformer Large Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote Paraformer Large INT8 to the active Open-XiaoAI Bridge service and the repository default without changing VAD, native speaker KWS, DeepSeek routing, or other user configuration.

**Architecture:** Apply narrow ASR-only configuration patches to the external runtime config and repository default, then restart the existing user service and prove the selected model and preserved invariants from fresh logs. Automated tests protect the ASR integration; a native-wake live utterance verifies the actual speaker-to-ASR path before the repository change is committed.

**Tech Stack:** Python configuration, sherpa-onnx 2.0.2, Paraformer Large INT8, systemd user service, pytest

**Spec:** `docs/superpowers/specs/2026-11-22-paraformer-large-promotion-design.md`

## Global Constraints

- Select backend `paraformer`, model directory `sherpa-onnx-paraformer-zh-2024-03-09`, and INT8 `True` explicitly in both configurations.
- Preserve native XiaoAI KWS, disabled Bridge/server-side KWS, VAD `250/1200/0.1`, direct DeepSeek routing, speaker `10.0.0.5`, and disabled `xiaozhi-esp32-server` startup.
- Do not change TTS, wake-word, VAD, DeepSeek, client, Hermes, or xiaozhi-server configuration.
- Preserve every existing repository `config.py` change outside the ASR selection hunk and never stage the pre-existing VAD hunk.
- Roll back only the active ASR fields to SenseVoice 2024 if startup, model loading, connectivity, invariants, or live recognition fail.

## Review Focus

- An explicit directory must select Paraformer Large rather than the installed trilingual model; Task 1 verifies the exact directory in both parsed configs and fresh logs.
- Restarting must not silently enable server-side KWS; Task 1 requires the fresh `VAD started, KWS disabled` log line.
- The existing 1200 ms pause tolerance must survive both edits; Task 1 parses both configs and verifies the fresh VAD log.
- A healthy process without a loaded recognizer is insufficient; Task 1 requires the exact backend, directory, and INT8 log after restart.
- A plausible DeepSeek answer is not proof of recognition; Task 2 accepts only the raw ASR transcript containing `大西洋有多大`.

---

### Task 1: Promote and verify the Paraformer Large runtime

**Files:**
- Modify: `/home/torrey/.config/open-xiaoai-bridge/config.py` (ASR selection fields only)
- Modify: `config.py:190-196` (ASR selection fields only)
- Test: `tests/test_sherpa_asr.py`
- Test: `tests/test_audio_service_flags.py`

**Interfaces:**
- Consumes: `APP_CONFIG["asr"]` from each configuration and the installed model directory under `core/models/`
- Produces: an active service and repository default selecting `paraformer` with exact Large INT8 model directory

- [ ] **Step 1: Capture the baseline and create a recoverable runtime-config backup**

Run read-only checks for `git status --short`, the repository `config.py` diff, parsed active ASR/VAD/audio-input values, `systemctl --user show open-xiaoai-bridge.service -p ActiveState -p SubState -p NRestarts -p ExecMainStatus`, and `systemctl --user is-enabled xiaozhi-esp32-server.service`. Copy `/home/torrey/.config/open-xiaoai-bridge/config.py` to a timestamped file in the same directory and record its exact path. Do not print provider credentials or the complete configuration.

Expected: Bridge is active with `NRestarts=0`; active ASR is SenseVoice 2024; VAD is `250/1200/0.1`; server-side KWS is disabled; `xiaozhi-esp32-server` is not enabled.

- [ ] **Step 2: Patch only the active and repository ASR selection fields**

Use narrow `apply_patch` hunks so each ASR mapping contains:

```python
"model": "paraformer",
"int8": True,
"model_dir": "sherpa-onnx-paraformer-zh-2024-03-09",
```

Do not modify or normalize surrounding lines. In repository `config.py`, retain the existing uncommitted VAD `min_silence_duration` change exactly as-is.

- [ ] **Step 3: Verify the static configuration and diff boundaries before restart**

Parse both files without importing application side effects and assert exact ASR fields plus VAD `250/1200/0.1`. Run `git diff -- config.py` and confirm it contains the pre-existing VAD hunk and exactly one new ASR hunk, with no other changes. Confirm `core/models/sherpa-onnx-paraformer-zh-2024-03-09/model.int8.onnx` exists.

Expected: both configurations explicitly select Paraformer Large INT8; no trilingual directory is selected; all invariants are unchanged.

- [ ] **Step 4: Run focused automated tests**

Run: `.venv/bin/python -m pytest tests/test_sherpa_asr.py tests/test_audio_service_flags.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Restart the service and inspect only fresh startup logs**

Record the current journal timestamp, run `systemctl --user restart open-xiaoai-bridge.service`, then inspect service state and journal entries since that timestamp.

Expected: `ActiveState=active`, `SubState=running`, `ExecMainStatus=0`, no unexpected restarts, and fresh logs prove backend `paraformer`, exact directory `sherpa-onnx-paraformer-zh-2024-03-09`, `int8=True`, VAD `250/1200/0.1`, `Audio input enabled (VAD started, KWS disabled)`, and speaker `10.0.0.5` connected.

- [ ] **Step 6: Verify routing and disabled services without exposing secrets**

Inspect only provider names, boolean enablement, endpoint hostnames, and service states. Do not print API keys.

Expected: direct DeepSeek remains selected; Hermes is absent/disabled; `xiaozhi-esp32-server` is inactive and not enabled; native speaker KWS remains the wake path.

- [ ] **Step 7: Run the complete automated test suite**

Run: `.venv/bin/python -m pytest -q`

Expected: the established suite passes (baseline previously observed as 83 passed, 3 skipped), with no new failures.

- [ ] **Step 8: Roll back active ASR only if any runtime gate fails**

Restore only active ASR fields to `sense_voice`, `True`, and `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`; restart and repeat Steps 5-6. Leave unrelated active settings and repository changes untouched, report the failed gate, and stop before live testing or commit.

Expected on success path: no rollback is needed.

### Task 2: Validate the live native-wake path and commit only the promotion

**Files:**
- Modify: `config.py:190-196` (already changed by Task 1)
- Verify: `/home/torrey/.config/open-xiaoai-bridge/config.py`

**Interfaces:**
- Consumes: the healthy Paraformer Large runtime produced by Task 1 and a user-spoken native-wake utterance
- Produces: a raw ASR transcript proving `大西洋有多大` and a repository commit containing only the ASR selection hunk

- [ ] **Step 1: Start a bounded live-log observation window**

Record a fresh journal timestamp and ask the user to say `小爱同学` followed by `大西洋有多大`. Inspect only entries from that window, separating wake/recording handoff, raw ASR transcript, and downstream DeepSeek response.

Expected: the speaker's native KWS hands audio to the Bridge and the raw local transcript contains `大西洋有多大` exactly after punctuation/space normalization.

- [ ] **Step 2: Roll back active ASR only if the live transcript is wrong**

If the raw transcript is absent or incorrect, restore the active ASR fields to SenseVoice 2024, restart and re-run the runtime health checks. Keep the benchmark evidence, do not stage the repository promotion, report the observed transcript, and stop for user direction.

Expected on success path: no rollback is needed.

- [ ] **Step 3: Build and inspect an ASR-only index patch**

Stage only the repository ASR hunk from `config.py` using an explicit patch or another non-interactive hunk-selective method. Do not stage the pre-existing VAD hunk or any unrelated untracked files. Inspect `git diff --cached -- config.py`, `git diff -- config.py`, and `git status --short`.

Expected: cached diff contains only the three ASR selection lines; the VAD `500 -> 1200` hunk remains unstaged; unrelated files remain untouched.

- [ ] **Step 4: Commit the repository default promotion**

Run: `git commit -m "config: promote Paraformer Large ASR"`

Expected: commit succeeds and its diff contains only the intended repository ASR selection hunk.

- [ ] **Step 5: Perform final acceptance checks**

Verify the service remains active with zero unexpected restarts, re-parse active and repository ASR fields, re-check the preserved VAD/KWS/DeepSeek/speaker/xiaozhi-server invariants, and inspect `git status --short` to confirm all pre-existing unrelated changes remain present and unstaged.

Expected: all six acceptance criteria in the design spec are satisfied.
