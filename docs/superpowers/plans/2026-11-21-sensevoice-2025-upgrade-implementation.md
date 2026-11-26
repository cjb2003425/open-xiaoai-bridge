# SenseVoice 2025 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate and activate the official SenseVoice 2025 INT8 model only if it recognizes both `大西洋` and `大象` correctly while keeping local inference below one second.

**Architecture:** Install the candidate beside the current model, benchmark both against identical PCM, and switch only the active configuration for live testing. Promote the repository default only after all six contrast utterances pass; otherwise restore the active 2024 model without adding correction rules.

**Tech Stack:** Python 3.12, Sherpa-ONNX 1.12.28, ONNX Runtime CPU, systemd user service, Intel N100.

**Spec:** `docs/superpowers/specs/2026-11-21-sensevoice-2025-upgrade-design.md`

## Global Constraints

- Candidate model is exactly `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09` from the official Sherpa-ONNX release asset.
- Preserve `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` as the rollback model.
- Every warm candidate decode must finish below `1.0` second; candidate average latency at each tested duration must be no more than 25% slower than the baseline.
- Candidate process RSS must remain at or below 800 MiB and leave at least 1 GiB system memory available.
- Do not add replacements, forced hotwords, homophone rules, a cloud correction request, or a second live recognizer.
- Do not change native XiaoAI KWS, recorder handoff, VAD, DeepSeek, TTS, or speaker-client behavior.
- Preserve unrelated working-tree changes, including the existing VAD edit in `config.py` and unrelated untracked documents.

## Review Focus

- Partial 165,783,878-byte archive: byte-size, `bzip2 -t`, member, and required-file checks must pass before installation.
- Misleading performance comparison: both models must decode identical 16 kHz mono PCM slices after a separate warmup.
- Ambiguous quality fix: `大西洋有多大` and `大象有多大` must each pass 3/3 in raw ASR logs.
- Broken runtime promotion: startup must retain SenseVoice INT8, VAD 250/1200/0.1, disabled server KWS, and the `10.0.0.5` connection.
- Dirty-worktree contamination: only the ASR `model_dir` hunk and this plan may be committed after acceptance.

---

### Task 1: Install and validate the candidate model

**Files:**
- Create, ignored by Git: `core/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/`
- Preserve: `core/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/`

**Interfaces:**
- Consumes: official asset `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2`
- Produces: a separate candidate directory with readable `model.int8.onnx` and `tokens.txt`

- [x] Verify at least 2 GiB free disk, 2 GiB available memory, and no incomplete destination directory.
- [x] Download into a `mktemp -d` directory with redirect following, HTTP failure detection, and retries; do not write directly into `core/models/`.
- [x] Require archive size `165783878` bytes, record SHA-256, run `bzip2 -t`, and verify one expected top-level directory containing `model.int8.onnx` and `tokens.txt`.
- [x] Extract into the temporary directory, require both files to be non-empty, then move only the validated top-level directory into `core/models/`.
- [x] Source `/home/torrey/.config/open-xiaoai-bridge/environment` without printing it and instantiate `OfflineRecognizer.from_sense_voice` using candidate INT8, `language="auto"`, `use_itn=True`, `num_threads=2`, and `provider="cpu"`; a one-second decode must complete without exception.
- [x] Record installed file sizes and directory size; remove only the task-specific temporary directory after successful installation.

### Task 2: Benchmark candidate against the active model

**Files:**
- Read: `/tmp/open-xiaoai-kws-live-snapshot.wav`
- Read: `core/services/audio/asr/sherpa.py`
- No repository files modified.

**Interfaces:**
- Consumes: both SenseVoice directories and identical 16 kHz mono PCM slices
- Produces: load time, three warm decode times and transcripts for 3/5/10-second inputs, RSS, and available-memory evidence

- [x] Validate the snapshot is 16-bit, mono, 16 kHz, and at least ten seconds long; abort benchmarking if it is unsuitable.
- [x] Run the 2024 baseline in its own process: load separately, warm with one second, then decode identical 3/5/10-second slices three times each; record average, maximum, transcript, RSS, and available memory.
- [x] Run the 2025 candidate in a fresh process with the identical procedure and PCM offsets.
- [x] Pass performance only if every candidate decode is below 1.0 second, each duration's average is at most 1.25× baseline, RSS is at most 800 MiB, available memory is at least 1 GiB, and no decode is empty or raises an exception.
- [x] Keep the active service and `/home/torrey/.config/open-xiaoai-bridge/config.py` unchanged throughout this task.

### Task 3: Temporarily promote the candidate

**Files:**
- Modify outside Git: `/home/torrey/.config/open-xiaoai-bridge/config.py:190-197`
- Preserve: `config.py`

**Interfaces:**
- Consumes: a passing Task 2 report and exact candidate directory
- Produces: active `asr.model="sense_voice"`, `asr.int8=True`, and `asr.model_dir="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"`

- [x] Confirm Task 2 passed, the current service is active, and the old active `model_dir` is exactly the 2024 directory.
- [x] Change only `asr.model_dir` in the active configuration to the exact 2025 directory.
- [x] Restart `open-xiaoai-bridge.service` and require one stable active process with no traceback or restart loop.
- [x] From fresh startup logs require SenseVoice 2025 INT8, VAD minimum speech 250 ms, silence 1200 ms, threshold 0.1, server KWS disabled, and speaker `10.0.0.5` connected.
- [x] On any failure, execute Task 5 immediately and do not run live quality tests.

### Task 4: Run the six-turn live quality gate and promote the default

**Files:**
- Modify after all six turns pass: `config.py:190-197`
- Modify: `docs/superpowers/plans/2026-11-21-sensevoice-2025-upgrade-implementation.md`

**Interfaces:**
- Consumes: healthy candidate runtime from Task 3
- Produces: six raw transcript results and, only on 6/6 success, repository default `asr.model_dir` set to the 2025 directory

- [ ] Mark a journal start time, then ask the user for three independent native-wake turns saying `大西洋有多大`; require three raw transcripts containing `大西洋有多大` and one wake/answer path per turn.
- [ ] Ask for three independent native-wake turns saying `大象有多大`; require three raw transcripts containing `大象有多大` and one wake/answer path per turn.
- [ ] Treat any wrong, empty, duplicated, or missing transcript as failure; execute Task 5 without adding replacements or another correction layer.
- [ ] After 6/6 success, add `"model_dir": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"` to the repository ASR config, leaving every other line unchanged.
- [ ] Run the focused regression suite:

  ```bash
  .venv/bin/python -m unittest tests.test_native_kws_takeover tests.test_wakeup_keywords tests.test_audio_stream tests.test_openai -v
  ```

  Expected: all tests pass.

- [ ] Inspect `git diff --check` and the exact config diff. Stage only the ASR `model_dir` subhunk (split and reject the pre-existing VAD subhunk) plus this plan; do not stage unrelated files.
- [ ] Commit the accepted default with message `config: select SenseVoice 2025 model`.

### Task 5: Roll back a failed candidate

**Files:**
- Modify on failure outside Git: `/home/torrey/.config/open-xiaoai-bridge/config.py:190-197`
- Preserve: `config.py`

**Interfaces:**
- Consumes: any Task 2, 3, or 4 failure after the candidate is installed
- Produces: active 2024 SenseVoice configuration and healthy service; candidate files remain installed

- [x] If the active configuration was changed, restore only `asr.model_dir` to `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`.
- [x] Restart the user service and require active status, SenseVoice 2024 INT8, VAD 250/1200/0.1, server KWS disabled, and `10.0.0.5` connected.
- [x] Leave the candidate directory installed for analysis; do not modify the speaker or add fallback correction rules.
- [x] Record which gate failed and stop this implementation path.

## Execution Outcome

Installation, loading, and offline performance passed. The 2025 candidate failed the first live quality group because one target utterance was transcribed as `大箱有多大`; later correct repetitions do not satisfy the required 3/3 gate. The active service was restored to the 2024 model, and the repository default was not changed.
