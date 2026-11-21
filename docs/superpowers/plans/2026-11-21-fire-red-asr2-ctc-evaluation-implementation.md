# FireRedASR2 CTC Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate FireRedASR2 CTC INT8 offline and integrate it as a selectable local ASR backend only if it meets the exact accuracy and three-second latency gate.

**Architecture:** Keep the active SenseVoice service untouched while validating and benchmarking the candidate through the installed sherpa-onnx factory. A failed offline gate ends the work without tracked adapter changes; a passing gate unlocks a capability-isolated adapter backend, regression tests, and a reversible live-speaker trial.

**Tech Stack:** Python 3.13, sherpa-onnx 2.0.2, ONNX Runtime CPU, NumPy, unittest, systemd user services

**Spec:** `docs/superpowers/specs/2026-11-21-fire-red-asr2-ctc-evaluation-design.md`

## Global Constraints

- Candidate is exactly `sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25` from the official sherpa-onnx release asset.
- Do not use Docker or Hermes.
- Keep native XiaoAI KWS, current VAD, direct DeepSeek, TTS, and boot-service behavior unchanged.
- Keep SenseVoice 2024 active throughout download and offline evaluation.
- Run with `num_threads=2`, `decoding_method="greedy_search"`, `debug=False`, and `provider="cpu"`.
- Promotion requires all nine speech-containing benchmark runs to contain `嘿小智` after punctuation/spacing normalization and every run to complete in at most 3.0 seconds.
- Preserve all pre-existing tracked and untracked user changes; never stage unrelated files.
- A failed offline gate forbids adapter implementation and live activation.

## Review Focus

- An explicit FireRedASR2 CTC model directory missing `tokens.txt` must report that exact missing file; pin this in Task 2.
- INT8 disabled must select `model.onnx`, not silently reuse `model.int8.onnx`; pin this in Task 2.
- FireRedASR2 CTC must never receive unsupported homophone-replacer keyword arguments even when dictionary assets exist; pin this in Task 2.
- Existing SenseVoice must continue receiving homophone-replacer arguments when those assets exist; pin this in Task 2.
- Qwen3-ASR must remain token-free and its factory arguments must not change; retain and run its existing coverage in Task 2.

---

### Task 1: Validate, install, and benchmark the candidate offline

**Files:**
- Create, ignored: `core/models/sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25/`
- Create, ignored: `.superpowers/sdd/2026-11-21-fire-red-asr2-ctc-evaluation-implementation/benchmark_fire_red_ctc.py`
- Create, ignored: `.superpowers/sdd/2026-11-21-fire-red-asr2-ctc-evaluation-implementation/offline-results.json`
- Preserve: `/home/torrey/.config/open-xiaoai-bridge/config.py`

**Interfaces:**
- Consumes: `sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(model: str, tokens: str, num_threads: int, decoding_method: str, debug: bool, provider: str)` and `/tmp/open-xiaoai-kws-live-snapshot.wav`.
- Produces: a validated candidate model directory and a Boolean offline-gate decision consumed by Task 2.

- [ ] **Step 1: Record the preflight state**

Record the current commit, dirty paths, `sherpa_onnx.__version__`, ONNX Runtime version, active ASR fields without printing secrets, bridge service state/restart count, xiaozhi service state, and SHA-256 of the active config. Require sherpa-onnx 2.0.2, an active SenseVoice 2024 bridge, disabled server KWS, and inactive `xiaozhi-esp32-server` before continuing.

- [ ] **Step 2: Download the official archive to a unique temporary directory**

Use:

`https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25.tar.bz2`

Do not download into `core/models/`. Record final byte size and SHA-256.

- [ ] **Step 3: Validate the archive before extraction**

Run `bzip2 -t`, list members without extracting, reject absolute paths or `..` traversal, and require exactly one top-level directory named `sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25` containing non-empty `model.int8.onnx` and `tokens.txt` members.

- [ ] **Step 4: Extract and install the model**

Extract only after Step 3 passes, validate installed non-empty `model.int8.onnx` and `tokens.txt`, then move the validated top-level directory atomically into `core/models/`. If a complete directory already exists, verify and reuse it; if an incomplete directory exists, stop rather than overwrite it.

- [ ] **Step 5: Smoke-test the direct factory**

Instantiate `from_fire_red_asr_ctc` with the exact runtime arguments in Global Constraints and decode the known speech-containing three-second region beginning at 11 seconds. Require a non-empty transcript and no exception.

- [ ] **Step 6: Create the isolated benchmark runner**

The runner accepts model directory, WAV path, `--start-seconds`, and `--expected-text`; validates mono 16-bit 16 kHz input and ten seconds of remaining audio; runs 3-, 5-, and 10-second slices three times; and emits JSON containing audio identity, expected text, load time, every transcript/time, per-duration averages/maxima, RSS, available memory, and swap.

- [ ] **Step 7: Run the benchmark on verified speech**

Run against `/tmp/open-xiaoai-kws-live-snapshot.wav`, start 11 seconds, expected text `嘿小智`. Require its SHA-256 to equal `a552ff98cc47ab058ddf4946d7a0643efee30fc79628b8872e4a79336bccee26` before decoding.

- [ ] **Step 8: Apply the offline gate**

Normalize only Unicode punctuation and whitespace for comparison. Mark PASS only when all nine normalized transcripts contain `嘿小智`, every decode is at most 3.0 seconds, the process is stable, and `VmSwap` is zero. Record the decision and exact reason. If FAIL, verify the active config hash and service state are unchanged, mark Tasks 2–4 skipped, and stop the plan.

- [ ] **Step 9: Clean temporary download artifacts**

Remove or recoverably trash only the unique task temporary directory. Retain a validated installed candidate and benchmark JSON. This task has no tracked commit.

### Task 2: Add the conditional FireRedASR2 CTC adapter backend

**Files:**
- Modify: `core/services/audio/asr/sherpa.py`
- Modify: `tests/test_sherpa_asr.py`

**Interfaces:**
- Consumes: Task 1 PASS and installed `model.int8.onnx` plus `tokens.txt`.
- Produces: backend key `fire_red_asr_ctc`, model selection through `_get_required_model_files(backend: str) -> dict[str, str]`, and an exact `from_fire_red_asr_ctc` factory call consumed by Task 3.

- [ ] **Step 1: Write failing model-layout tests**

Add unittest cases named `test_fire_red_asr_ctc_selects_int8_model`, `test_fire_red_asr_ctc_selects_float_model`, and `test_fire_red_asr_ctc_reports_missing_tokens`. Assert backend keyword `fire-red-asr2-ctc`, `model.int8.onnx`/`model.onnx` selection from `asr.int8`, and an explicit error containing `tokens.txt` for an incomplete configured directory.

- [ ] **Step 2: Run the layout tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_sherpa_asr.SherpaASRModelLayoutTest -v`

Expected: FAIL because `fire_red_asr_ctc` is unknown.

- [ ] **Step 3: Add the minimal backend model metadata**

In `_BACKENDS`, add `fire_red_asr_ctc` with `dir_keyword="fire-red-asr2-ctc"`, `factory="from_fire_red_asr_ctc"`, `model_files={"model": {True: "model.int8.onnx", False: "model.onnx"}}`, `extra_kwargs={"decoding_method": "greedy_search"}`, and `uses_homophone_replacer=False`. Add it to the module's supported-backend documentation.

- [ ] **Step 4: Run the layout tests and verify GREEN**

Run the Step 2 command.

Expected: all layout tests pass.

- [ ] **Step 5: Write failing factory-capability tests**

Add `test_fire_red_asr_ctc_factory_omits_homophone_arguments` with temporary model, tokens, dictionary, FST, and lexicon files. Patch the factory and assert the exact call contains `model`, `tokens`, `num_threads=2`, `decoding_method="greedy_search"`, `debug=False`, and `provider="cpu"`, with no `hr_dict_dir`, `hr_rule_fsts`, or `hr_lexicon`. Strengthen the existing SenseVoice test to assert those three homophone arguments remain present.

- [ ] **Step 6: Run the factory tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_sherpa_asr.SherpaASRFactoryTest -v`

Expected: FireRedASR2 CTC test fails because the shared legacy path still passes unsupported homophone arguments.

- [ ] **Step 7: Gate homophone arguments by backend capability**

Keep `tokens.txt` handling for all token-using backends. Add homophone arguments only when `spec.get("uses_homophone_replacer", True)` is true. Do not alter Qwen's token-free path or existing model discovery behavior.

- [ ] **Step 8: Run focused and full tests**

Run:

`.venv/bin/python -m unittest tests.test_sherpa_asr -v`

`.venv/bin/python -m unittest discover -s tests -v`

Expected: focused tests pass; full suite passes with only the three existing explicit live-test skips.

- [ ] **Step 9: Commit the backend**

Stage only `core/services/audio/asr/sherpa.py` and `tests/test_sherpa_asr.py`.

Commit: `feat: add FireRedASR2 CTC backend`

### Task 3: Verify the adapter against the installed model

**Files:**
- Modify, ignored: `.superpowers/sdd/2026-11-21-fire-red-asr2-ctc-evaluation-implementation/offline-results.json`
- Preserve: `/home/torrey/.config/open-xiaoai-bridge/config.py`

**Interfaces:**
- Consumes: Task 2 `fire_red_asr_ctc` backend and Task 1 installed model.
- Produces: adapter smoke/benchmark evidence and a live-trial eligibility decision consumed by Task 4.

- [ ] **Step 1: Load the candidate through `_SherpaASR` without editing configuration files**

Mock only `asr.model="fire_red_asr_ctc"`, `asr.model_dir="sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25"`, and `asr.int8=True` in-process. Decode the same three-second speech segment and require normalized text containing `嘿小智`.

- [ ] **Step 2: Re-run the nine-run gate through the adapter factory contract**

Use the same WAV hash, 11-second start, durations, repetitions, runtime arguments, normalization, and 3.0-second per-run threshold as Task 1. Require no swap use and record adapter results alongside direct-factory results.

- [ ] **Step 3: Verify service and configuration invariants**

Require the active config hash to match Task 1 preflight, `open-xiaoai-bridge.service` active with no new restarts, active SenseVoice 2024 logs, native KWS behavior unchanged, server KWS disabled, `xiaozhi-esp32-server` inactive, and speaker `10.0.0.5` connected.

- [ ] **Step 4: Re-run the full suite**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass with only the three explicit live-test skips. A failed adapter gate or regression makes Task 4 ineligible and leaves SenseVoice active.

### Task 4: Run a reversible live-speaker trial

**Files:**
- Temporarily modify: `/home/torrey/.config/open-xiaoai-bridge/config.py`
- Preserve: repository `config.py` and every unrelated external setting

**Interfaces:**
- Consumes: Task 3 PASS and exact active-config backup/hash.
- Produces: user acceptance of FireRedASR2 CTC or a verified SenseVoice 2024 rollback.

- [ ] **Step 1: Back up and hash the active configuration**

Create a permission-preserving backup in a unique task temporary directory. Record the original ASR fields and hash without printing API keys or secrets.

- [ ] **Step 2: Patch only the three ASR selection fields**

Set `asr.model="fire_red_asr_ctc"`, `asr.model_dir="sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25"`, and `asr.int8=True`. Do not alter VAD, KWS, DeepSeek, TTS, speaker address, or service enablement.

- [ ] **Step 3: Restart and verify the bridge**

Require an active service, `NRestarts=0` for the new activation, no traceback, a log naming the exact backend/model directory, native wake events, and speaker `10.0.0.5` connection. Roll back immediately on failure.

- [ ] **Step 4: Ask the user to perform the live utterance set**

Test `嘿小智`, `大西洋`, two or three difficult foreign names chosen by the user, one Chinese-English mixed sentence, and one longer sentence with a short pause. Record the user's accuracy and responsiveness judgment without storing unrelated captured speech.

- [ ] **Step 5: Apply the live acceptance gate**

Accept only with no empty results or instability, acceptable perceived latency, and user-confirmed overall accuracy better than SenseVoice. If accepted, leave FireRedASR2 CTC active and record final service/config state.

- [ ] **Step 6: Roll back on rejection or failure**

Restore only the original ASR fields from the backup, restart, and require SenseVoice 2024 startup, current VAD values, disabled server KWS, inactive xiaozhi service, direct DeepSeek, unchanged TTS, speaker connection, active service, and no restart loop.

- [ ] **Step 7: Final verification**

Run the focused ASR tests and full unittest suite once more. Report model identity, benchmark table, memory use, live result, final active ASR, service health, commits, and preserved unrelated dirty paths.
