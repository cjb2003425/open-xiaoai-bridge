# sherpa-onnx 2.0.2 and Qwen3-ASR Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing environment to sherpa-onnx 2.0.2 and temporarily test Qwen3-ASR 0.6B INT8 on the speaker only when offline recognition stays within three seconds.

**Architecture:** First prove sherpa-onnx 2.0.2 can run the existing SenseVoice path and preserve a complete dependency rollback. Add Qwen as a separate offline-recognizer backend, validate and install the official model beside SenseVoice, then gate a temporary live configuration behind identical-audio latency, memory, and transcript checks.

**Tech Stack:** Python 3.12, uv 0.11, sherpa-onnx 2.0.2, ONNX Runtime 1.23.2 CPU, pytest/unittest, systemd user service, Qwen3-ASR 0.6B INT8

**Spec:** `docs/superpowers/specs/2026-11-21-sherpa-onnx-2-qwen3-asr-evaluation-design.md`

## Global Constraints

- Do not use Docker.
- Pin `sherpa-onnx==2.0.2`; keep `onnxruntime==1.23.2` unless lock resolution proves the combination impossible.
- Keep native XiaoAI KWS, server-side KWS disabled, VAD 250/1200/0.1, DeepSeek, and speaker `10.0.0.5` unchanged.
- Do not reconnect Hermes or start `xiaozhi-esp32-server`.
- Preserve the active and repository SenseVoice 2024 fallback and every unrelated dirty-worktree change.
- Source `/home/torrey/.config/open-xiaoai-bridge/environment` without tracing or printing it before Python commands that load sherpa-onnx.
- Qwen may reach the speaker only if every representative offline decode is at most `3.0` seconds and the host remains stable.
- Qwen remains temporary until the user accepts the observed live accuracy and latency.

## Review Focus

- Dynamic-library environment omitted: imports and tests must run with the service environment loaded, while logs must never expose its secrets (Task 1).
- 2.0.2 breaks an existing factory or KWS import: SenseVoice construction, focused regressions, and service startup must pass before Qwen work continues (Task 1).
- A file named `tokenizer` or an incomplete tokenizer directory is accepted: validation must require the directory plus `vocab.json`, `merges.txt`, and `tokenizer_config.json` (Task 2).
- A partial or wrong 878,702,423-byte archive is installed: byte size, `bzip2 -t`, top-level member, and required-path checks must precede extraction into `core/models/` (Task 3).
- Qwen fails or exceeds the gate after active configuration changes: restore only the ASR model fields to SenseVoice 2024, restart, and verify the complete native-wake path (Task 5).

---

### Task 1: Upgrade sherpa-onnx with a tested dependency rollback

**Files:**
- Modify: `pyproject.toml:19`
- Modify mechanically: `uv.lock`
- Preserve: `/home/torrey/.config/open-xiaoai-bridge/config.py`
- Create outside Git: `/tmp/open-xiaoai-sherpa-2-rollback/`

**Interfaces:**
- Consumes: current lock at sherpa-onnx 1.12.28, active SenseVoice 2024 service, and the systemd environment file
- Produces: locked `sherpa-onnx==2.0.2`, its exact `sherpa-onnx-core` dependency, a healthy SenseVoice 2024 service, and a verified, ready-to-run 1.12.28 rollback procedure

- [ ] **Step 1: Capture the exact baseline and rollback inputs**

  Record the current commit, dirty status, Python/uv/package versions, `systemctl --user is-active`, `ExecStart`, `EnvironmentFiles`, active ASR fields, VAD fields, and recent startup logs without printing environment-file contents. Copy only `pyproject.toml` and `uv.lock` into the task-specific rollback directory.

- [ ] **Step 2: Cache and verify the current Linux x86-64 1.12.28 wheel**

  Download the exact cp312 manylinux wheel recorded in the current lockfile into the rollback directory and require size `4247236` and SHA-256 `0261c9552b3c72bfd219887c7d8f2957198d4dfcf4a53693b26a0eee5019b668`.

- [ ] **Step 3: Pin and resolve 2.0.2**

  Change only `sherpa-onnx>=1.12.26` to `sherpa-onnx==2.0.2`, run `uv lock`, and inspect the diff. Require sherpa-onnx 2.0.2 plus its exact core package, retain ONNX Runtime 1.23.2, and reject unrelated dependency churn.

- [ ] **Step 4: Stop, synchronize, and smoke-test the existing backend**

  Stop `open-xiaoai-bridge.service`, run `uv sync --locked`, then source the service environment and assert:

  ```python
  import sherpa_onnx
  assert sherpa_onnx.__version__ == "2.0.2"
  assert hasattr(sherpa_onnx.OfflineRecognizer, "from_sense_voice")
  assert hasattr(sherpa_onnx.OfflineRecognizer, "from_qwen3_asr")
  ```

  Construct SenseVoice 2024 INT8 through the current adapter and decode a valid one-second 16 kHz mono PCM slice without exception.

- [ ] **Step 5: Run compatibility tests and restore service**

  Run with the environment loaded:

  ```bash
  .venv/bin/python -m unittest tests.test_native_kws_takeover tests.test_wakeup_keywords tests.test_audio_stream tests.test_audio_service_flags tests.test_openai -v
  ```

  Expected: all tests pass. Restart the service and require active status, SenseVoice 2024 INT8 startup, VAD 250/1200/0.1, no server KWS startup, no Hermes/xiaozhi-server connection, and speaker `10.0.0.5` connected.

- [ ] **Step 6: Exercise rollback if any Step 3–5 check fails**

  Restore the saved dependency files, synchronize the virtual environment from the old lock (using the verified wheel if network resolution fails), restart the service, and require the Step 5 SenseVoice health checks. Stop the Qwen plan after rollback.

- [ ] **Step 7: Commit the accepted dependency upgrade**

  ```bash
  git add pyproject.toml uv.lock
  git commit -m "build: pin sherpa-onnx 2.0.2"
  ```

### Task 2: Add the Qwen3-ASR backend with path-safe validation

**Files:**
- Modify: `core/services/audio/asr/sherpa.py:1-159`
- Create: `tests/test_sherpa_asr.py`

**Interfaces:**
- Consumes: `sherpa_onnx.OfflineRecognizer.from_qwen3_asr(...)` from Task 1
- Produces: backend key `qwen3_asr`; `_get_required_model_dirs(backend: str) -> dict[str, str]`; `_find_model_dir(keyword: str, required_files: dict[str, str], required_dirs: dict[str, str]) -> str`; Qwen factory arguments `conv_frontend`, `encoder`, `decoder`, and `tokenizer`

- [ ] **Step 1: Write failing backend and model-layout tests**

  Add tests asserting that `qwen3_asr` selects:

  - `conv_frontend.onnx`;
  - `encoder.int8.onnx` / `decoder.int8.onnx` when `asr.int8=True`;
  - `encoder.onnx` / `decoder.onnx` when false; and
  - a directory argument `tokenizer` pointing to `tokenizer/`.

  Assert that a missing tokenizer directory, a regular file named `tokenizer`, or a tokenizer directory missing any of `vocab.json`, `merges.txt`, and `tokenizer_config.json` raises `FileNotFoundError` naming the missing path. Keep equivalent SenseVoice file-validation assertions to prevent regression.

- [ ] **Step 2: Run the layout tests and verify failure**

  Run: `.venv/bin/python -m pytest tests/test_sherpa_asr.py -q`

  Expected: FAIL because `qwen3_asr` and directory validation do not exist.

- [ ] **Step 3: Add the Qwen backend specification and directory validation**

  Extend `_BACKENDS` with `dir_keyword="qwen3-asr"`, `factory="from_qwen3_asr"`, the three file arguments above, `model_dirs={"tokenizer": "tokenizer"}`, and Qwen defaults `max_total_len=512`, `max_new_tokens=128`, `temperature=1e-6`, `top_p=0.8`, `seed=42`, `hotwords=""`. Existing backends keep their current file rules and implicit `tokens.txt` requirement.

  Implement `_get_required_model_dirs(backend: str) -> dict[str, str]` and extend `_find_model_dir(...)` so Qwen validates a real tokenizer directory and its three nested files. Do not weaken explicit `model_dir` errors or automatic directory scanning.

- [ ] **Step 4: Run the layout tests and verify success**

  Run: `.venv/bin/python -m pytest tests/test_sherpa_asr.py -q`

  Expected: the backend/layout tests pass.

- [ ] **Step 5: Write the failing recognizer-factory tests**

  Patch `OfflineRecognizer.from_qwen3_asr` and assert one call with exact model paths, `num_threads=2`, `debug=False`, `provider="cpu"`, and the six Qwen defaults. Assert the call contains neither `tokens` nor homophone-replacer arguments. Also assert the existing SenseVoice path still passes `tokens`, `language="auto"`, `use_itn=True`, and available homophone-replacer files.

- [ ] **Step 6: Run factory tests and verify failure**

  Run: `.venv/bin/python -m pytest tests/test_sherpa_asr.py -q`

  Expected: Qwen factory test fails because `_ensure_loaded()` always supplies `tokens` and the homophone arguments.

- [ ] **Step 7: Route Qwen and legacy factory arguments separately**

  In `_ensure_loaded()`, combine validated model-file and model-directory arguments. For Qwen, call `from_qwen3_asr` with only its paths, runtime options, and Qwen defaults. For legacy backends, retain `tokens.txt` and existing homophone behavior unchanged. Update the module docstring to list Qwen3-ASR.

- [ ] **Step 8: Run focused and full regression tests**

  Run with the service environment loaded:

  ```bash
  .venv/bin/python -m pytest tests/test_sherpa_asr.py -q
  .venv/bin/python -m unittest discover -s tests -v
  ```

  Expected: all tests pass; no test accesses a real model or external service.

- [ ] **Step 9: Commit the backend**

  ```bash
  git add core/services/audio/asr/sherpa.py tests/test_sherpa_asr.py
  git commit -m "feat: add Qwen3 ASR backend"
  ```

### Task 3: Download and validate the official Qwen3-ASR model

**Files:**
- Create, ignored by Git: `core/models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/`
- Create, ignored by Git: `.superpowers/sdd/2026-11-21-sherpa-onnx-2-qwen3-asr-evaluation/model-install.txt`

**Interfaces:**
- Consumes: official release asset `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2`
- Produces: validated local model files `conv_frontend.onnx`, `encoder.int8.onnx`, `decoder.int8.onnx`, and complete `tokenizer/`

- [ ] **Step 1: Check installation preconditions**

  Require at least 4 GiB available disk, at least 4 GiB available memory, no existing incomplete destination, an active SenseVoice fallback, and a task-specific directory from `mktemp -d`.

- [ ] **Step 2: Download and validate before extraction**

  Download into the temporary directory with redirect following, HTTP failure detection, and retries. Require exactly `878702423` bytes, record SHA-256, run `bzip2 -t`, and inspect the archive member list for one exact top-level model directory and the required paths. Do not extract directly into `core/models/`.

- [ ] **Step 3: Extract and atomically install**

  Extract in the temporary directory; require every model file and tokenizer file to be non-empty; then move only the validated top-level directory to `core/models/`. Record sizes and SHA-256 values in the ignored install ledger.

- [ ] **Step 4: Smoke-test the installed factory and adapter**

  With the service environment loaded, construct `OfflineRecognizer.from_qwen3_asr` using the Task 2 defaults, decode the model's bundled short Chinese test WAV, then load the same directory through `_SherpaASR` with a mocked Qwen configuration. Both must return non-empty text without exception.

- [ ] **Step 5: Clean temporary data without touching installed models**

  Remove only the resolved task-specific temporary directory after validation. If validation fails, leave the active service and SenseVoice configuration unchanged and stop before Task 4.

### Task 4: Benchmark Qwen against SenseVoice on identical audio

**Files:**
- Read: `/tmp/open-xiaoai-kws-live-snapshot.wav`
- Create, ignored by Git: `.superpowers/sdd/2026-11-21-sherpa-onnx-2-qwen3-asr-evaluation/benchmark_asr.py`
- Create, ignored by Git: `.superpowers/sdd/2026-11-21-sherpa-onnx-2-qwen3-asr-evaluation/benchmark-sensevoice.json`
- Create, ignored by Git: `.superpowers/sdd/2026-11-21-sherpa-onnx-2-qwen3-asr-evaluation/benchmark-qwen3.json`

**Interfaces:**
- Consumes: identical 16 kHz mono PCM slices and both installed models
- Produces: per-model load time, warmup, three 3/5/10-second decodes, raw transcripts, maximum RSS, available memory, and a pass/fail decision

- [ ] **Step 1: Validate and freeze benchmark input**

  Require 16-bit mono 16 kHz WAV with at least ten seconds of audio; record its byte size and SHA-256. The same starting sample and duration slices must be used for both models.

- [ ] **Step 2: Write the isolated benchmark runner**

  Accept `backend`, `model_dir`, and `wav_path`; construct exactly one recognizer; run a separate one-second warmup; then time three decodes each at 3, 5, and 10 seconds. Emit JSON containing sherpa version, audio metadata/hash, model load and decode times, unmodified transcripts, `ru_maxrss`, and `/proc/meminfo` available memory.

- [ ] **Step 3: Benchmark SenseVoice and Qwen in separate processes**

  Run SenseVoice 2024 first and Qwen second with the environment loaded. Do not modify active configuration. Require non-empty output and no exception in every run.

- [ ] **Step 4: Apply the offline gate**

  Pass only if every Qwen timed decode is at most `3.0` seconds, repeated decodes are stable, the process is not killed or swapping uncontrollably, and at least 1 GiB remains available. Report Qwen versus SenseVoice latency and raw transcripts; do not hide a wrong proper noun behind replacements.

- [ ] **Step 5: Stop or advance without ambiguity**

  If any gate fails, keep SenseVoice 2024 active, retain 2.0.2 only because Task 1 passed, record the failed metric, and stop. If all gates pass, proceed to Task 5.

### Task 5: Run the temporary six-turn speaker trial and choose the final active state

**Files:**
- Modify temporarily outside Git: `/home/torrey/.config/open-xiaoai-bridge/config.py:190-197`
- Preserve: repository `config.py` and all unrelated active configuration fields
- Create, ignored by Git: `.superpowers/sdd/2026-11-21-sherpa-onnx-2-qwen3-asr-evaluation/live-trial.txt`

**Interfaces:**
- Consumes: passing Task 4 results and installed Qwen backend/model
- Produces: six raw live transcripts plus either user-accepted active Qwen configuration or verified active SenseVoice 2024 rollback

- [ ] **Step 1: Back up and narrowly switch the active ASR fields**

  Record the active config hash and back it up. Change only `asr.model` to `qwen3_asr`, keep `asr.int8=True`, and set `asr.model_dir` to `sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25` using `apply_patch`.

- [ ] **Step 2: Restart and verify the complete startup state**

  Restart `open-xiaoai-bridge.service`; require stable active status, Qwen model startup, VAD 250/1200/0.1, server KWS absent, native XiaoAI handoff available, DeepSeek target unchanged, `xiaozhi-esp32-server` inactive/disabled, no Hermes connection, and speaker `10.0.0.5` connected. On any failure, execute Step 6.

- [ ] **Step 3: Collect three independent `大西洋有多大` turns**

  Mark the journal start time and ask the user for one native-wake utterance at a time. Record exactly one raw ASR transcript and one response path per turn; repeat until three independent turns are captured.

- [ ] **Step 4: Collect three independent `大象有多大` turns**

  Repeat the same one-turn-at-a-time procedure until three independent raw transcripts are captured. Treat empty, duplicated, missing, or mismatched raw transcripts as failures; do not add replacement rules during the trial.

- [ ] **Step 5: Present accuracy and latency for user acceptance**

  Report 3/3 results for each contrast phrase, offline Qwen latency, observed live timing from end-of-recording to ASR log, and any stability warnings. Ask whether to keep Qwen active; do not alter the repository default automatically.

- [ ] **Step 6: Roll back any rejected or failed Qwen trial**

  Restore only `asr.model="sense_voice"`, `asr.int8=True`, and `asr.model_dir="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"`; restart and require SenseVoice 2024, VAD 250/1200/0.1, server KWS absent, DeepSeek unchanged, and speaker `10.0.0.5` connected.

- [ ] **Step 7: Verify and report the final state**

  If accepted, leave only the active external configuration on Qwen and verify a final stable turn. In either outcome, run `git status --short`, `git diff --check`, the focused ASR tests, and service health checks; report exact commits, model directory, dependency version, benchmark results, and active/fallback model.
