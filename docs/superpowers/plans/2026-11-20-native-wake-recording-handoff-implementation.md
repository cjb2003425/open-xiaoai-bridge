# Native-Wake Recording Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the first syllables after the L06A native acknowledgement from being clipped by avoiding a redundant recorder restart.

**Architecture:** Branch only the initial setup in `ExternalConversationController._conversation_loop()`. Native wake sessions (`initial_notify=False`) keep the already-running PCM stream, while ordinary sessions (`initial_notify=True`) retain the existing stop → notify → start sequence. Later VAD, ASR, TTS, and multi-turn recorder behavior remains unchanged.

**Tech Stack:** Python 3, `asyncio`, `unittest`, `unittest.mock`, systemd user service.

**Spec:** `docs/superpowers/specs/2026-11-20-native-wake-recording-handoff-design.md`

## Global Constraints

- Do not change KWS routing, VAD parameters, ASR model selection, DeepSeek, TTS, Rust Client behavior, or buffering internals.
- Do not add a readiness sound, fixed delay, or pre-roll buffer.
- Preserve the user's existing changes in `config.py` and unrelated untracked planning/spec files.
- Use `apply_patch` for source and test edits; do not use destructive Git commands.

### Task 1: Encode the initial handoff contract with tests

**Files:**
- Modify: `tests/test_native_kws_takeover.py:159-176`
- Test: `tests/test_native_kws_takeover.py`

- [x] Change `test_initial_notify_can_be_skipped` so `initial_notify=False` asserts `_stop_recording`, `_play_notify`, and `_start_recording` are all not awaited.
- [x] Add a test for `initial_notify=True` that records mock side effects and asserts the exact order `stop`, `notify`, `start`.
- [x] Keep `controller.active = False` in both tests so only initial setup is exercised and the turn loop exits without VAD dependencies.
- [x] Run the focused test before production code changes:

  ```bash
  .venv/bin/python -m unittest tests.test_native_kws_takeover.NativeKwsConversationTest -v
  ```

  Expected RED: the native-wake test fails because stop/start are currently awaited. The normal-notify order test should pass.

### Task 2: Skip recorder restart for native wake

**Files:**
- Modify: `core/external_conversation.py:175-188`
- Test: `tests/test_native_kws_takeover.py`

- [x] Move `_stop_recording()`, its log, `_play_notify()`, and `_start_recording()` inside the `if self.initial_notify:` branch.
- [x] Update the nearby comment to state that stop → notify → start applies only when an initial notification is requested; the native path reuses the active PCM stream.
- [x] Leave `logger.debug("Ready to listen", ...)` after the branch so both paths retain a readiness trace.
- [x] Do not modify stop/start behavior elsewhere in the controller.
- [x] Re-run the focused test and confirm both cases pass:

  ```bash
  .venv/bin/python -m unittest tests.test_native_kws_takeover.NativeKwsConversationTest -v
  ```

- [x] Run the relevant regression suite:

  ```bash
  .venv/bin/python -m unittest tests.test_native_kws_takeover tests.test_wakeup_keywords tests.test_audio_stream tests.test_openai -v
  ```

- [x] Inspect `git diff --check` and `git diff -- core/external_conversation.py tests/test_native_kws_takeover.py` to confirm the patch is scoped.
- [x] Commit only the implementation, its tests, and this plan; do not stage `config.py` or unrelated untracked files.

### Task 3: Verify the live speaker handoff

**Files:**
- Verify only: `/home/torrey/.config/open-xiaoai-bridge/config.py`

- [x] Restart and check the user service:

  ```bash
  systemctl --user restart open-xiaoai-bridge.service
  systemctl --user is-active open-xiaoai-bridge.service
  journalctl --user -u open-xiaoai-bridge.service -n 120 --no-pager
  ```

- [x] Confirm startup logs show SenseVoice, VAD silence duration `1200 ms`, and speaker `10.0.0.5` connected.
- [x] Ask the user to say `大西洋有多大` immediately after the native `小爱同学` acknowledgement, with no leading filler phrase.
- [x] Confirm only one acknowledgement/answer path runs in a clean repeat.
- [ ] Confirm the ASR transcript retains `大西洋`. Not met: two live attempts produced `大象有多大`; onset `大` is retained, but SenseVoice still collapses `西洋` to `象`.
- [ ] If the live test regresses recorder availability, restore the prior initial branch with `apply_patch`, rerun the focused tests, and restart the service; do not alter speaker-side configuration.

## Review Focus

- Native `initial_notify=False` invokes none of stop, notify, or start; protected by the focused native-wake test.
- Ordinary `initial_notify=True` preserves exact stop → notify → start order; protected by the new order test.
- Later turn behavior remains untouched; checked by the scoped diff and regression suite.
- Test loops terminate deterministically with `active=False`; verified by the focused test completing without VAD setup.
- Runtime retains SenseVoice, 1200 ms endpointing, and `10.0.0.5`; verified from service logs before the live phrase test.

## Execution Outcome

The recorder-handoff change passed focused and regression tests, and the live speaker retained the initial `大` instead of the prior clipped `气阳`. The exact `大西洋` acceptance phrase remains a separate SenseVoice lexical recognition failure (`大西洋` → `大象`) and was not masked with an unsafe global replacement.
