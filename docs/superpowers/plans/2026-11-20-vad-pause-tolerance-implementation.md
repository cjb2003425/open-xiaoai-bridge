# VAD Pause Tolerance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase the bridge's utterance-ending silence window from 500 ms to 1200 ms.

**Architecture:** This is a configuration-only adjustment to the existing VAD endpoint detector. Existing VAD, ASR, native KWS, and DeepSeek code paths remain unchanged.

**Tech Stack:** Python configuration, systemd user service, journalctl

**Spec:** `docs/superpowers/specs/2026-11-20-vad-pause-tolerance-design.md`

## Global Constraints

- Set only `APP_CONFIG["vad"]["min_silence_duration"]` to exactly `1200` milliseconds.
- Keep VAD threshold `0.10` and minimum speech duration `250` milliseconds.
- Do not alter KWS, ASR, DeepSeek routing, or conversation timeouts.

## Review Focus

- Wrong configuration section: verify `vad`, not `kws`, is changed.
- Unit mismatch: verify the value is `1200` milliseconds, not `1.2`.
- Accidental sensitivity change: verify threshold remains `0.10`.
- Service regression: verify the user service remains active.
- Ineffective reload: verify runtime logs show a 1200 ms VAD silence duration.

---

### Task 1: Increase the VAD silence window

**Files:**
- Modify: `config.py:178-184`
- Verify: `docs/superpowers/specs/2026-11-20-vad-pause-tolerance-design.md`

**Interfaces:**
- Consumes: `APP_CONFIG["vad"]`, read by `core/services/audio/vad/__init__.py`
- Produces: an integer `min_silence_duration` value of `1200` milliseconds

- [ ] **Step 1: Verify the current baseline**

Run a read-only Python AST check and confirm the VAD silence duration is currently `500`, the threshold is `0.10`, and the minimum speech duration is `250`.

- [ ] **Step 2: Change the configuration**

Set `APP_CONFIG["vad"]["min_silence_duration"]` in `config.py` to `1200` without changing neighboring VAD values.

- [ ] **Step 3: Verify the static configuration**

Run the AST check again. Expected: silence duration `1200`, threshold `0.10`, minimum speech duration `250`; KWS silence duration remains `480`.

- [ ] **Step 4: Verify runtime application**

Inspect recent service logs for the configuration reload. If no reload is observed, restart `open-xiaoai-bridge.service` in the user session, then confirm it is active and logs `最小静音时长=1200ms`.

- [ ] **Step 5: Perform the user acceptance check**

Ask the user to speak one request with a roughly one-second pause. Expected: one ASR request rather than two truncated requests.
