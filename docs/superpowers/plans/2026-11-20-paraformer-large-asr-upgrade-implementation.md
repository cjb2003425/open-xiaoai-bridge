# Paraformer-Large ASR Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install and evaluate the official Paraformer Chinese-English INT8 model, then activate it only if it stays below one second for ten-second audio and passes user quality review.

**Architecture:** Keep the candidate beside the current SenseVoice and trilingual Paraformer models. Exercise both recognizers offline on identical PCM, collect latency, memory, and transcript evidence, and make activation a conditional configuration-only step with a tested SenseVoice rollback.

**Tech Stack:** Python 3.12, Sherpa-ONNX 1.12.28, ONNX Runtime CPU, systemd user service, Intel N100

**Spec:** `docs/superpowers/specs/2026-11-20-paraformer-large-asr-upgrade-design.md`

## Global Constraints

- Candidate model is exactly `sherpa-onnx-paraformer-zh-2024-03-09` INT8 from the official Sherpa-ONNX release asset.
- Every warm ten-second candidate decode must finish below 1.0 second.
- SenseVoice and the existing trilingual Paraformer model must remain installed and usable.
- Do not add FunASR/PyTorch, SeACo, Fun-ASR-Nano, or a second network service.
- Do not change VAD, native XiaoAI KWS, DeepSeek, TTS, or speaker-client behavior.
- Do not activate the candidate until the user accepts the side-by-side transcript comparison.

## Review Focus

- Partial or corrupt 996,591,364-byte release archive: validate archive readability and required files before extraction.
- Directory ambiguity: pin `asr.model_dir` to the exact candidate directory before activation.
- Cold-start distortion: report model load separately and measure warm decode runs for the one-second gate.
- Misleading quality result: use identical audio for both models and include Mandarin, English-in-Chinese, and foreign-name phrases.
- Failed activation or speaker reconnect: restore SenseVoice and restart the service if startup logs do not confirm the candidate and speaker connection.

---

### Task 1: Install and validate the candidate model

**Files:**
- Create, ignored by Git: `core/models/sherpa-onnx-paraformer-zh-2024-03-09/`
- Preserve: `core/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/`
- Preserve: `core/models/sherpa-onnx-paraformer-trilingual-zh-cantonese-en/`

**Interfaces:**
- Consumes: official release URL `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-paraformer-zh-2024-03-09.tar.bz2`
- Produces: candidate directory containing readable `model.int8.onnx` and `tokens.txt`

- [ ] **Step 1: Verify capacity and absence of a conflicting destination**

Run `df -B1 /home/torrey`, `free -b`, and an exact-path existence check. Require at least 5 GiB free disk, 2 GiB available memory, and either no destination or an already complete candidate directory.

- [ ] **Step 2: Download to a task-specific temporary directory**

Use `mktemp -d`, download with redirect following, retries, and failure-on-HTTP-error, and retain the original archive name. Expected HTTP asset size: `996591364` bytes.

- [ ] **Step 3: Validate the archive before extraction**

Check the exact byte size, run `bzip2 -t`, list the archive, and assert that it contains one top-level candidate directory plus `model.int8.onnx` and `tokens.txt`. Abort without touching `core/models/` on any failure.

- [ ] **Step 4: Extract and validate the model**

Extract into `core/models/`, assert the required files are non-empty, and instantiate `sherpa_onnx.OfflineRecognizer.from_paraformer` with `num_threads=2` and `provider="cpu"`. A one-second sample must decode without an exception.

- [ ] **Step 5: Record installation evidence**

Record archive SHA-256, extracted directory size, required file sizes, and the installed Sherpa-ONNX version. Do not add model files to Git.

### Task 2: Run offline performance and quality comparison

**Files:**
- Read: `/tmp/open-xiaoai-kws-live-snapshot.wav`
- Read: `core/services/audio/asr/sherpa.py`
- No repository files modified

**Interfaces:**
- Consumes: the candidate and current SenseVoice model directories, identical 16 kHz mono PCM
- Produces: load time, three warm decode times for each 3/5/10-second input, peak/RSS memory, and paired transcripts

- [ ] **Step 1: Validate benchmark audio**

Use Python's `wave` module to assert one channel, 16 kHz sample rate, 16-bit samples, and at least ten seconds of audio. If the retained snapshot is unsuitable, capture a new user-approved test recording without changing the live ASR route.

- [ ] **Step 2: Benchmark SenseVoice baseline**

Instantiate SenseVoice INT8 exactly as the bridge does. Measure load time separately, warm it with one second of audio, then run three decodes each for 3, 5, and 10 seconds. Preserve transcripts and report average and maximum latency.

- [ ] **Step 3: Benchmark the candidate**

Repeat the identical procedure with the pinned Paraformer candidate. Pass only if all three warm ten-second runs are below `1.0` second.

- [ ] **Step 4: Check memory and failures**

Record process RSS after each model warmup and inspect output for empty transcripts, exceptions, or repeated-token failures. Reject the candidate on persistent memory pressure, a crash, or repeated load/decode errors.

- [ ] **Step 5: Present side-by-side quality evidence**

Compare identical phrases covering ordinary Mandarin, Chinese with English terms, and foreign personal names. Ask the user to accept or reject the candidate. Do not continue to activation without explicit acceptance.

### Task 3: Activate the accepted candidate

**Files:**
- Modify: `config.py:190-198`
- Modify outside Git: `/home/torrey/.config/open-xiaoai-bridge/config.py:190-198`

**Interfaces:**
- Consumes: an explicit user acceptance from Task 2
- Produces: `asr.model="paraformer"`, `asr.model_dir="sherpa-onnx-paraformer-zh-2024-03-09"`, and `asr.int8=True` in both configurations

- [ ] **Step 1: Verify the activation preconditions**

Confirm the Task 2 performance gate passed, user quality acceptance is explicit, required model files still exist, and the active service is healthy before editing either configuration.

- [ ] **Step 2: Update the repository configuration**

Set the three exact ASR values while leaving VAD, KWS, DeepSeek, TTS, and all other configuration unchanged.

- [ ] **Step 3: Update the active configuration**

Apply the same three ASR values to `/home/torrey/.config/open-xiaoai-bridge/config.py` and verify both files match for those keys.

- [ ] **Step 4: Restart and verify runtime selection**

Restart `open-xiaoai-bridge.service` in the user session. Require `active` status, an ASR startup log naming `model=paraformer` and the exact candidate path, and a speaker connection log. Treat any traceback, repeated restart, or wrong model path as failure.

- [ ] **Step 5: Run a live acceptance phrase**

Ask the user to speak one ordinary Mandarin question and one question containing a foreign name. Confirm both complete within the expected endpoint delay and route to DeepSeek normally.

- [ ] **Step 6: Commit the repository configuration**

Commit only `config.py` and this plan if it is not already committed, preserving unrelated working-tree changes.

### Task 4: Roll back on activation failure

**Files:**
- Modify when required: `config.py:190-198`
- Modify when required outside Git: `/home/torrey/.config/open-xiaoai-bridge/config.py:190-198`

**Interfaces:**
- Consumes: a failed Task 3 runtime or live acceptance check
- Produces: restored `model="sense_voice"`, `int8=True`, and a SenseVoice model path with a healthy service

- [ ] **Step 1: Restore SenseVoice configuration when required**

Set `model="sense_voice"`, keep `int8=True`, and pin `model_dir="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"` in both configuration files.

- [ ] **Step 2: Restart and verify rollback when required**

Require active service status, a SenseVoice startup log with the pinned path, and a speaker connection log. Retain the candidate directory for offline testing; do not delete existing models.
