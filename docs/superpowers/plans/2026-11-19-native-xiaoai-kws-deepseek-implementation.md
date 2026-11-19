# Native XiaoAI KWS to DeepSeek Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the L06A native `小爱同学` detector as a wake-only signal, suppress Xiaomi locally, acknowledge with `我在`, and route the next locally transcribed utterance directly to DeepSeek.

**Architecture:** A persistent Rust Client guard parses the first empty native `SpeechRecognizer.RecognizeResult`, suppresses Xiaomi, plays the local acknowledgement, and emits a deduplicated `native_kws` event.  The Bridge disables only its server-side KWS, keeps PCM/VAD/local ASR, accepts that event, and starts an OpenAI-compatible conversation without a duplicate ready sound.

**Tech Stack:** Rust 2021/Tokio/Serde, Python 3.12/asyncio/unittest, systemd user service, OpenWrt procd shell scripts, Linaro ARMv7 toolchain, DeepSeek OpenAI-compatible API.

**Spec:** `docs/superpowers/specs/2026-11-19-native-xiaoai-kws-deepseek-design.md`

## Global Constraints

- Do not use Docker or `cross`; ARM builds use the installed Linaro compiler and Rust `armv7-unknown-linux-gnueabihf` target.
- Preserve the PCM tap and `LD_PRELOAD`; only the `OPEN_XIAOAI_BLOCK_NATIVE_WAKE` mode changes.
- Never commit, print, or log the DeepSeek API key; keep it only in `/home/torrey/.config/open-xiaoai-bridge/environment` with mode `0600`.
- Never fall back to Hermes, xiaozhi, Xiaomi ASR, or Xiaomi answers.
- Keep `AUDIO_INPUT_ENABLE=1`, `OPENAI_ENABLE=1`, and `openai.input_mode="local_asr"`.
- Default/failure mode remains `OPEN_XIAOAI_BLOCK_NATIVE_WAKE=1` until the Client guard and Bridge event path are healthy.
- Preserve unrelated dirty-worktree changes in both repositories; commit only files named by each task.
- Do not persist native-wake allowance or reboot the speaker until temporary physical acceptance passes.

## Review Focus

- A malformed or partial instruction-log line must not trigger suppression, acknowledgement, or a Bridge event (Task 1 tests).
- Repeated markers for one `dialog_id`, including after log rotation, must yield one takeover only (Tasks 1 and 2 tests).
- A disconnected Bridge must still suppress Xiaomi but must not play `我在` or queue a stale event for reconnection (Task 2 tests).
- A false/invalid `native_kws` payload must not start DeepSeek, and bounded deduplication must not grow forever (Task 5 tests).
- A Client or Bridge failure during the controlled mode switch must restore native-wake blocking and leave the PCM tap healthy (Tasks 3 and 7 checks).

---

### Task 1: Native Wake Marker Parser and Ordered Local Actions

**Files:**
- Create: `/home/torrey/workspace/open-xiaoai/packages/client-rust/src/services/monitor/native_wake.rs`
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/src/services/monitor/mod.rs`

**Interfaces:**
- Consumes: `FileMonitorEvent`, `instruction::LogMessage`, and local `utils::shell::CommandResult`.
- Produces: `NativeWakePayload { dialog_id: String, local_suppressed: bool, ack_played: bool }`, `DialogDeduper::admit(&mut self, dialog_id: &str) -> bool`, `native_wake_plan(event: &FileMonitorEvent, connected: bool, deduper: &mut DialogDeduper) -> Option<NativeWakePlan>`, and async `execute_native_wake<R, Fut>(plan: NativeWakePlan, runner: R) -> NativeWakePayload` where the runner accepts one shell command at a time.

- [ ] **Step 1: Write failing Rust unit tests in `native_wake.rs`**

Test exact acceptance of an empty, non-final, `is_vad_begin=false` `SpeechRecognizer.RecognizeResult` with a non-empty `dialog_id`.  Test rejection of malformed JSON, empty IDs, non-empty text, final results, `is_vad_begin=true`, and other namespaces/names.  Test bounded deduplication and assert that the executor invokes suppression before `tts_play.sh '我在'`, records each result, and omits acknowledgement when `connected=false`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cargo test --manifest-path packages/client-rust/Cargo.toml native_wake -- --nocapture`

Expected: FAIL because `native_wake` and its interfaces do not exist.

- [ ] **Step 3: Implement the parser, deduper, plan, and executor**

Use a maximum of 64 remembered `dialog_id` values.  The local suppression command must stop `tts_play.sh`/`miplayer`, pause `mphelper`, and toggle the existing pnshelper awake-off sequence before acknowledgement.  Do not interpolate instruction-log content into shell commands.

- [ ] **Step 4: Run Rust tests**

Run: `cargo test --manifest-path packages/client-rust/Cargo.toml native_wake -- --nocapture`

Expected: all `native_wake` tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git -C /home/torrey/workspace/open-xiaoai add packages/client-rust/src/services/monitor/native_wake.rs packages/client-rust/src/services/monitor/mod.rs
git -C /home/torrey/workspace/open-xiaoai commit -m "feat: parse native XiaoAI wake markers"
```

### Task 2: Persistent Client Takeover Guard

**Files:**
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/src/bin/client.rs`
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/src/services/connect/message.rs`
- Test: Rust unit tests colocated with the modified modules.

**Interfaces:**
- Consumes: Task 1's `native_wake_plan` and `execute_native_wake`.
- Produces: `MessageManager::is_connected(&self) -> bool` and a structured WebSocket event named `native_kws` whose data is `NativeWakePayload`.

- [ ] **Step 1: Write failing lifecycle tests**

Test connected and disconnected plans, event emission only after suppression and acknowledgement finish, no stale emission after reconnect, raw instruction forwarding for non-wake lines, and one event for duplicate markers.

- [ ] **Step 2: Run focused Rust tests and verify failure**

Run: `cargo test --manifest-path packages/client-rust/Cargo.toml --bin client native_wake -- --nocapture`

Expected: FAIL because the persistent guard and `is_connected` are absent.

- [ ] **Step 3: Start the instruction monitor once for the Client process**

Move instruction monitoring out of the per-WebSocket `init`/`dispose` lifecycle.  Its callback always executes local suppression for admitted markers; it plays `我在` and emits `native_kws` only while `MessageManager::is_connected()` is true.  Keep playing/KWS monitors scoped to each WebSocket connection.

- [ ] **Step 4: Run all Client Rust tests and formatter**

Run:

```bash
cargo fmt --manifest-path packages/client-rust/Cargo.toml -- --check
cargo test --manifest-path packages/client-rust/Cargo.toml
```

Expected: formatter clean and all tests PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git -C /home/torrey/workspace/open-xiaoai add packages/client-rust/src/bin/client.rs packages/client-rust/src/services/connect/message.rs
git -C /home/torrey/workspace/open-xiaoai commit -m "feat: guard native wake across reconnects"
```

### Task 3: Fail-Closed Native Wake Mode Switch

**Files:**
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/tap/l06a_pcm_tap.sh`
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/tap/build.sh`
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/tap/user.sh`
- Modify: `/home/torrey/workspace/open-xiaoai/packages/client-rust/tap/install_l06a.sh`

**Interfaces:**
- Consumes: existing tap component manifest and procd overlay manager.
- Produces: `$BASE/native-wake.mode` with only `blocked` or `takeover`; manager commands `block-native-wake` and `allow-native-wake`; absent/invalid mode resolves to `blocked` and renders `OPEN_XIAOAI_BLOCK_NATIVE_WAKE=1`.

- [ ] **Step 1: Add failing shell assertions to `build.sh`**

Assert that default/invalid mode renders value `1`, takeover mode renders value `0`, exactly one `procd_append_param env OPEN_XIAOAI_BLOCK_NATIVE_WAKE=<value>` is present, and mode changes restart to one healthy hooked `mipns-xiaomi` process or roll back to blocked.

- [ ] **Step 2: Run the build script and verify failure**

Run: `tmp_dir=$(mktemp -d); packages/client-rust/tap/build.sh "$tmp_dir"`

Expected: FAIL because mode-aware rendering and commands are absent.

- [ ] **Step 3: Implement the fail-closed mode file and commands**

`user.sh` continues to call `start`; `start` reads the mode file.  Installation writes `blocked` before first start.  `allow-native-wake` may write `takeover` only after component validation; `block-native-wake` writes `blocked`.  A failed restart restores `blocked`, restarts the hook, and returns nonzero.

- [ ] **Step 4: Build and run host tap tests**

Run: `tmp_dir=$(mktemp -d); packages/client-rust/tap/build.sh "$tmp_dir"`

Expected: `pcm tap tests passed`, manifest verification succeeds, and no Docker process is used.

- [ ] **Step 5: Commit Task 3**

```bash
git -C /home/torrey/workspace/open-xiaoai add packages/client-rust/tap/l06a_pcm_tap.sh packages/client-rust/tap/build.sh packages/client-rust/tap/user.sh packages/client-rust/tap/install_l06a.sh
git -C /home/torrey/workspace/open-xiaoai commit -m "feat: add fail-closed native wake takeover mode"
```

### Task 4: Disable Only Bridge-Side KWS

**Files:**
- Modify: `/home/torrey/workspace/open-xiaoai-bridge/core/app.py`
- Restore: `/home/torrey/workspace/open-xiaoai-bridge/core/services/audio/kws/__init__.py` (remove temporary continuous-stream diagnostics and PCM dump)
- Create: `/home/torrey/workspace/open-xiaoai-bridge/tests/test_audio_service_flags.py`
- Delete: `/home/torrey/workspace/open-xiaoai-bridge/tests/test_kws_continuous_stream.py`
- Modify: `/home/torrey/workspace/open-xiaoai-bridge/README.md`

**Interfaces:**
- Consumes: `AUDIO_INPUT_ENABLE` and new `KWS_ENABLE` runtime flags.
- Produces: audio startup where `AUDIO_INPUT_ENABLE=1,KWS_ENABLE=0` starts VAD and ASR support but never imports/starts `KWS`; `KWS_ENABLE` defaults to true for backward compatibility.

- [ ] **Step 1: Write failing startup tests**

Patch VAD and KWS objects and assert the four relevant flag combinations: audio off starts neither; audio on/KWS off starts only VAD; both on starts both; invalid/absent `KWS_ENABLE` uses the documented true default.  Assert local-ASR validation still requires audio input.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_audio_service_flags -v`

Expected: FAIL because `KWS_ENABLE` is ignored.

- [ ] **Step 3: Implement independent VAD/KWS startup and remove diagnostics**

Keep the existing `AUDIO_INPUT_ENABLE` behavior.  Start VAD whenever audio input is enabled for an enabled backend; conditionally import/start KWS only when `KWS_ENABLE` parses true.  Restore the KWS module to its pre-investigation behavior and remove `/tmp/open-xiaoai-kws-live.pcm` creation and periodic diagnostic logs.

- [ ] **Step 4: Run audio tests and compile checks**

Run:

```bash
.venv/bin/python -m unittest tests.test_audio_service_flags tests.test_audio_stream -v
.venv/bin/python -m py_compile core/app.py core/services/audio/kws/__init__.py
```

Expected: all tests PASS and compilation succeeds.

- [ ] **Step 5: Commit Task 4**

```bash
git add core/app.py core/services/audio/kws/__init__.py tests/test_audio_service_flags.py tests/test_kws_continuous_stream.py README.md
git commit -m "feat: allow local audio without bridge KWS"
```

### Task 5: Bridge `native_kws` Routing and Ready-Sound Suppression

**Files:**
- Modify: `/home/torrey/workspace/open-xiaoai-bridge/core/xiaoai.py`
- Modify: `/home/torrey/workspace/open-xiaoai-bridge/core/wakeup_session.py`
- Modify: `/home/torrey/workspace/open-xiaoai-bridge/core/external_conversation.py`
- Modify: `/home/torrey/workspace/open-xiaoai-bridge/config.py`
- Create: `/home/torrey/workspace/open-xiaoai-bridge/tests/test_native_kws_takeover.py`
- Replace/Delete: `/home/torrey/workspace/open-xiaoai-bridge/tests/test_hey_xiaozhi_wakeup.py`

**Interfaces:**
- Consumes: Client event `{"event":"native_kws","data":{"dialog_id":str,"local_suppressed":bool,"ack_played":bool}}`.
- Produces: bounded `XiaoAI` deduplication (64 IDs), `EventManager.wakeup("小爱同学", "native_kws")`, `_start_openai_conversation(initial_notify: bool = True)`, and `ExternalConversationController(initial_notify: bool = True)`.

- [ ] **Step 1: Write failing event and conversation tests**

Assert valid events route once to `openai`; duplicates, empty IDs, wrong types, missing fields, and oversized payloads do not route.  Assert `local_suppressed=false` calls remote suppression once.  Assert `before_wakeup` for `native_kws` returns `openai` without playing text, while old `hey 小智`/`你好小黑` KWS inputs do not route.  Assert `initial_notify=false` skips only the first notify and still starts recording; later turn notifications remain unchanged.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_native_kws_takeover -v`

Expected: FAIL because `native_kws` is unhandled and initial notify is unconditional.

- [ ] **Step 3: Implement validated event handling and source-aware conversation startup**

Handle `native_kws` before the generic unknown-event branch.  Bound dedupe storage at 64 IDs.  Retry `_suppress_dialog(dialog_id, "native KWS local suppression failed")` only when the payload reports failure.  Pass `initial_notify=False` only for this source.  Set `APP_CONFIG["wakeup"]["keywords"]` to an empty list because no Bridge keyword is active.

- [ ] **Step 4: Run routing and regression tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_native_kws_takeover tests.test_wakeup_keywords tests.test_openai -v
.venv/bin/python -m py_compile core/xiaoai.py core/wakeup_session.py core/external_conversation.py config.py
```

Expected: all tests PASS and compilation succeeds.

- [ ] **Step 5: Commit Task 5**

```bash
git add core/xiaoai.py core/wakeup_session.py core/external_conversation.py config.py tests/test_native_kws_takeover.py tests/test_hey_xiaozhi_wakeup.py
git commit -m "feat: route native XiaoAI wake to DeepSeek"
```

### Task 6: Protected Runtime Configuration and DeepSeek Authentication

**Files:**
- Modify (not committed): `/home/torrey/.config/open-xiaoai-bridge/config.py`
- Modify (not committed): `/home/torrey/.config/open-xiaoai-bridge/environment`
- Verify: `/home/torrey/.config/systemd/user/open-xiaoai-bridge.service`

**Interfaces:**
- Consumes: `OPENAI_API_KEY`, `OPENAI_ENABLE=1`, `AUDIO_INPUT_ENABLE=1`, and `KWS_ENABLE=0`.
- Produces: runtime target `https://api.deepseek.com/v1`, model `deepseek-chat`, empty `session_header`, local ASR, and a configured non-logged API key.

- [ ] **Step 1: Update the external config and environment**

Apply the Task 5 wake hook/config changes to the external config.  Store the user-supplied key only as `OPENAI_API_KEY` in the external environment, add `KWS_ENABLE=0`, and run `chmod 600` on both runtime files.  Do not show file contents.

- [ ] **Step 2: Check secret containment without printing the key**

Run repository scans for `OPENAI_API_KEY=` and DeepSeek key prefixes, reporting filenames only.  Expected: no tracked repository file contains the runtime key; only the protected external environment supplies it.

- [ ] **Step 3: Perform a minimal authenticated DeepSeek request**

Source the protected environment without echoing it.  Send one `deepseek-chat` request with `max_tokens` no greater than 8 and verify HTTP 200 plus a non-empty assistant message.  Do not print request headers or the key.

- [ ] **Step 4: Restart and verify the Bridge**

Run `systemctl --user restart open-xiaoai-bridge.service`, then verify active status and logs for DeepSeek target, VAD started, Bridge KWS absent, and Client connection.  Expected: no Hermes/xiaozhi connection and no secret in logs.

### Task 7: Non-Docker ARM Build and Fail-Closed Temporary Deployment

**Files:**
- Build output only: `/home/torrey/workspace/open-xiaoai/packages/client-rust/target-linaro/armv7-unknown-linux-gnueabihf/release/client`
- Temporary speaker staging: `/tmp/open-xiaoai-native-kws-stage`

**Interfaces:**
- Consumes: Tasks 1-3 Client/tap artifacts and Tasks 4-6 running Bridge.
- Produces: a supervised Client with native-wake guard while the speaker remains in `blocked` mode.

- [ ] **Step 1: Run complete host verification**

Run Rust tests, Bridge focused tests, `git diff --check` in both repositories, and inspect both dirty worktrees so unrelated files are excluded from commits.

- [ ] **Step 2: Build ARM Client without Docker**

Run:

```bash
CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABIHF_LINKER=/opt/gcc-linaro-7.5.0-2019.12-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf-gcc \
cargo build --manifest-path packages/client-rust/Cargo.toml --release \
  --target armv7-unknown-linux-gnueabihf --target-dir packages/client-rust/target-linaro
```

Expected: an ARM EABI5 executable; `file` must not report x86-64.

- [ ] **Step 3: Stage and checksum artifacts on the speaker**

Copy the Client and tap bundle into `/tmp/open-xiaoai-native-kws-stage`, verify SHA-256 values on both hosts, preserve the current Client and overlay paths for rollback, and keep `native-wake.mode=blocked`.

- [ ] **Step 4: Start the new Client under the existing supervisor**

Verify one Client process, WebSocket connection, healthy PCM ring, loaded hook, `OPEN_XIAOAI_BLOCK_NATIVE_WAKE=1`, and Bridge VAD input.  Say `小爱同学` once; expected at this stage: complete silence because blocking is still enabled.

- [ ] **Step 5: Prove deployment rollback before allowing native wake**

Restore the previous temporary Client, verify reconnection and PCM health, then redeploy the new Client and repeat health checks.  Do not proceed unless both rollback and redeploy succeed.

### Task 8: Controlled Native Wake Acceptance and Persistence

**Files:**
- Runtime speaker mode: `/data/open-xiaoai/tap/native-wake.mode` only after temporary acceptance.
- Runtime logs: Bridge journal and speaker Client log.

**Interfaces:**
- Consumes: healthy temporary deployment from Task 7.
- Produces: accepted and then persistent `takeover` mode, or automatic restoration to `blocked`.

- [ ] **Step 1: Temporarily allow native wake**

Run the manager's `allow-native-wake`, then verify exactly one hooked `mipns-xiaomi` process, ring health, environment value `0`, Client health, and Bridge connection.  On any failure immediately run `block-native-wake` and stop.

- [ ] **Step 2: Run five wake-only trials**

For each `小爱同学`, verify exactly one local `我在`, one `native_kws` event, one Bridge route, and no Xiaomi TTS/NLP response.  Check unique `dialog_id` values and no duplicate routing.

- [ ] **Step 3: Run end-to-end DeepSeek trials**

After `我在`, ask at least two questions.  Verify local ASR text, a direct DeepSeek request, non-empty response playback, and no Hermes/xiaozhi/Xiaomi backend activity.

- [ ] **Step 4: Test disconnected-Bridge behavior**

Stop the Bridge, say `小爱同学`, and verify Xiaomi is suppressed with no `我在` and no stale event after reconnection.  Restart Bridge and verify the next fresh wake succeeds.

- [ ] **Step 5: Persist and reboot only after user acceptance**

Install the validated bundle under `/data`, write mode `takeover`, sync, and reboot the speaker.  After reboot verify supervisor, Client, hooked PCM tap, mode value `0`, Bridge connection, one wake, and one DeepSeek question.

- [ ] **Step 6: Final rollback proof and handoff**

Run `block-native-wake`, verify `小爱同学` is silent and PCM remains healthy, then return to `takeover` only if the user confirms the final desired state.  Report exact services, mode file, rollback command, test results, and note that the API key remains only in the protected environment.

