# SDD ledger — plan: /home/torrey/workspace/open-xiaoai-bridge/docs/superpowers/plans/2026-11-25-chatgpt-siwc-backend-implementation.md

- Execution base: `2ee443472b973a3563b490513cb1b0f31ccd87e5`
- Mode: inline execution; multi-agent delegation is disabled by the active session instructions.
- Ruling: referenced `test-driven-development`, `verification-before-completion`, and `finishing-a-development-branch` skill files are not installed. Apply the plan's explicit RED/GREEN tests and final verification manually.
- Finalization: the user explicitly requested that all remaining ASR/VAD configuration, design, plan, and SDD files be committed together.

## Interface preflight

| Task | Produced interface | Consumer | Status |
| --- | --- | --- | --- |
| 1 | API-key-only `OpenAIManager` | Existing OpenAI conversation and TTS paths | Verified |
| 2–4 | `ChatGPTAuthConfig`, credential store, `ChatGPTAuthManager`, CLI | ChatGPT manager and operators | Verified |
| 5 | `ChatGPTManager` Responses streaming API | Conversation controller and app lifecycle | Verified |
| 6 | `ChatGPTConversationController` and wake routing | Main application | Verified |
| 7 | Config/docs/live acceptance | Operators | Verified, including live ChatGPT plan usage |

## Progress

- [x] Task 1 — Restore OpenAI-compatible API-key-only operation
  - RED: `tests/test_openai.py` failed because the old OAuth config was still parsed.
  - GREEN: `29 passed` for `tests/test_openai.py tests/test_openai_tts.py tests/test_tts_router.py`.
- [x] Task 2 — SIWC configuration and protected credential storage
  - RED: `8 failed`; `core.chatgpt_auth` and SIWC ignore rule did not exist.
  - GREEN: `8 passed, 13 subtests passed`; `uv lock --check` resolved cleanly.
- [x] Task 3 — PKCE login, callback, and OIDC validation
  - RED: login-focused run failed on missing `ChatGPTAuthManager` (9 failures).
  - GREEN: `17 passed, 24 subtests passed` for the complete auth test module.
- [x] Task 4 — Models, refresh, logout, and CLI
  - RED: lifecycle run failed on eight missing methods; CLI run failed on four missing entry points.
  - GREEN: `30 passed, 24 subtests passed` with no warnings.
  - Ruling: revocation endpoint is discovered from the official OIDC metadata as required by the current OpenAI SIWC documentation.
- [x] Task 5 — Responses streaming and wake-session history
  - RED: all 13 manager tests failed because `core.chatgpt` did not exist.
  - GREEN: `19 passed` for `tests/test_chatgpt.py tests/test_tts_router.py`.
- [x] Task 6 — Application lifecycle and wake routing
  - RED: routing suite reported eight expected gaps across controller, flags, dispatch, cleanup, and startup scripts.
  - GREEN: `21 passed` including dual-backend lifecycle and Native KWS takeover; `bash -n scripts/start.sh` passed.
- [x] Task 7 — Configuration, documentation, regression, and live acceptance
  - RED: final contract run exposed four expected config/documentation/native-route gaps.
  - GREEN (automated): final full suite `149 passed, 3 skipped, 29 subtests passed`; lock and whitespace checks passed.
  - Live acceptance: browser SIWC login succeeded, selected `gpt-6.2-sol`, and two smoke tests in separate processes both returned `连接成功`.
  - Security: credential and persistent host-ID files are mode `0600` and ignored by Git; no token contents were printed or committed.
