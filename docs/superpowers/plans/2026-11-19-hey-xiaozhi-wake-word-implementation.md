# “Hey 小智” Wake Word Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make “Hey 小智” the only local custom wake phrase and route it to the direct DeepSeek-compatible conversation path.

**Architecture:** Keep wake-word recognition in the existing Sherpa KWS service. Narrow the active keyword list to one phrase and change the KWS routing hook so a result containing “小智” selects `openai`, while the disabled XiaoZhi backend is never selected.

**Tech Stack:** Python 3, asyncio hooks, Sherpa ONNX keyword generation, unittest, systemd user service.

**Spec:** `docs/superpowers/specs/2026-11-19-hey-xiaozhi-wake-word-design.md`

## Global Constraints

- `wakeup.keywords` contains exactly `hey 小智`.
- KWS “Hey 小智” routes to `openai`, plays “我在”, and exits with “小智，再见”.
- Prior custom wake phrases do not trigger local KWS.
- XiaoZhi remains disabled and Hermes is not reintroduced.
- Native “小爱同学” blocking remains unchanged.
- Keep the repository config and active external config behavior identical.

## Review Focus

- Sherpa may return `HEY小智` without a space; routing must match on “小智”.
- English casing in runtime results must not affect routing.
- Old “你好小黑” input must return no route and play nothing.
- The generated keyword file must have one non-empty line and one result label.
- A missing DeepSeek key must not prevent KWS startup or the “我在” acknowledgement.

---

### Task 1: Wake Hook and Configuration

**Files:**
- Create: `tests/test_hey_xiaozhi_wakeup.py`
- Modify: `config.py`
- Modify: `/home/torrey/.config/open-xiaoai-bridge/config.py`

**Interfaces:**
- Consumes: `before_wakeup(speaker, text, source, app)` and `after_wakeup(speaker, source, session_key)`.
- Produces: the single keyword list `['hey 小智']` and KWS route result `openai`.

- [ ] **Step 1: Add failing async hook/config tests**

Test that the keyword list is exact, `before_wakeup(..., 'HEY小智', 'kws', ...)` plays “我在” and returns `openai`, “你好小黑” returns `None` with no playback, and `after_wakeup(..., source='openai')` plays “小智，再见”.

- [ ] **Step 2: Run the focused tests and confirm current behavior fails**

Run: `.venv/bin/python -m unittest tests.test_hey_xiaozhi_wakeup -v`

Expected: failures showing the old multi-keyword list, `xiaozhi` route, and old prompt strings.

- [ ] **Step 3: Implement the minimal repository config change**

Replace the KWS “小黑” and “小智” branches with one “小智” branch that plays “我在” and returns `openai`; change the OpenAI exit prompt; replace the keyword list with one lowercase-English entry.

- [ ] **Step 4: Mirror the same behavior into the active external config**

Apply the same three behavioral changes to `/home/torrey/.config/open-xiaoai-bridge/config.py` without changing its DeepSeek URL, model, empty session header, or credential handling.

- [ ] **Step 5: Run tests and syntax checks**

Run: `.venv/bin/python -m unittest tests.test_hey_xiaozhi_wakeup tests.test_wakeup_keywords -v && .venv/bin/python -m py_compile config.py /home/torrey/.config/open-xiaoai-bridge/config.py && git diff --check`

Expected: all tests pass and both configs compile.

---

### Task 2: Keyword Regeneration and Live Validation

**Files:**
- Regenerate: `core/models/keywords.txt` (runtime model asset, not committed unless already tracked by project policy)

**Interfaces:**
- Consumes: Task 1 active config and `OPENAI_ENABLE=1`.
- Produces: one Sherpa keyword entry for `HEY小智` and a running Bridge KWS service.

- [ ] **Step 1: Regenerate keywords from the active config**

Run the keyword generator with the service environment and active `CONFIG_PATH`; fail if it returns nonzero.

- [ ] **Step 2: Verify the generated asset**

Assert `keywords.txt` has exactly one non-empty line and its result label contains `HEY小智`.

- [ ] **Step 3: Restart Bridge and verify readiness**

Restart `open-xiaoai-bridge.service`; verify active state, direct DeepSeek target in startup logs, KWS/VAD startup, and the speaker Client reconnection.

- [ ] **Step 4: Perform spoken acceptance**

Ask the user to say “你好小黑”, “Hey 小智”, and “小爱同学”. Expected: only “Hey 小智” triggers, plays “我在”, and logs an `openai` route; native “小爱同学” stays silent.

- [ ] **Step 5: Commit after acceptance**

Commit only the repository config, focused test, plan, and directly related existing DeepSeek configuration support. Do not commit secrets, external runtime config, generated model binaries, or unrelated worktree changes.
