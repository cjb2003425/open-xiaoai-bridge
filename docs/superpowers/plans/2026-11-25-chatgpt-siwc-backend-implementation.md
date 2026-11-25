# ChatGPT SIWC Voice Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tool-free ChatGPT voice backend that consumes an eligible user's ChatGPT plan through official SIWC while preserving a separate API-key OpenAI-compatible backend.

**Architecture:** `ChatGPTAuthManager` owns official SIWC login, validation, protected credentials, refresh, model discovery, and logout. `ChatGPTManager` owns wake-session history and streams the official Responses API through `AsyncOpenAI`; the existing `OpenAIManager` remains a separate Chat Completions adapter for API-key services such as DeepSeek.

**Tech Stack:** Python 3.12+, `openai>=2.34,<3`, `aiohttp`, `PyJWT[crypto]>=2.10,<3`, `asyncio`, `unittest`/`pytest`

**Spec:** `docs/superpowers/specs/2026-11-25-chatgpt-siwc-backend-design.md`

## Global Constraints

- Keep `openai` and `chatgpt` as independent backends enabled by `OPENAI_ENABLE` and `CHATGPT_ENABLE`.
- `openai` uses API-key Chat Completions and retains custom `base_url` support; remove its generic `oauth_device` mode.
- `chatgpt` uses only official SIWC endpoints and `https://api.openai.com/v1`; it accepts neither API keys nor custom base URLs.
- ChatGPT inference must send `store=False`, `stream=True`, SDK `max_retries=0`, full wake-session history, and no tools or SIWC-unsupported fields.
- Bind OAuth callback only to `127.0.0.1`, default port `1455`, exact path `/auth/callback`; support SSH local-port forwarding.
- Persist credentials atomically with mode `0600`; never log tokens, authorization codes, PKCE verifiers, or authorization URLs containing `id_token_hint`.
- Keep history only within one ChatGPT wake session; a new wake starts empty.
- TTS stays independently configured and never uses SIWC credentials.
- Preserve unrelated working-tree changes, especially existing ASR/VAD edits in `config.py` and unrelated untracked design/plan files.

## Review Focus

- A forwarded callback reaching the correct port but wrong path or wrong `state` must fail closed and leave saved credentials unchanged; Task 3 pins this.
- Reauthorization whose callback supplies a different issued `client_id` must be rejected instead of switching account registrations; Task 3 pins this.
- Concurrent refresh at process restart must consume one rotating refresh token once and persist its replacement before releasing waiters; Task 4 pins this.
- A stream that emits text and then fails or ends without `response.completed` must not enter history or reach TTS; Task 5 pins this.
- With both `OPENAI_ENABLE` and `CHATGPT_ENABLE`, routing, interruption, shutdown, configuration, and history must remain independent; Task 6 pins this.

---

## File Structure

- Delete `core/oauth_device.py` and `tests/test_oauth_device.py`: remove the superseded generic Device OAuth implementation.
- Modify `core/openai.py` and `tests/test_openai.py`: restore API-key-only OpenAI-compatible Chat Completions behavior on the SDK.
- Create `core/chatgpt_auth.py`: SIWC config, credentials, secure store, PKCE login, OIDC validation, refresh, model catalog, and logout.
- Create `tests/test_chatgpt_auth.py`: isolated protocol, security, persistence, refresh, model, and revocation tests.
- Create `core/chatgpt.py`: Responses streaming, wake-session history, request tracking, and TTS routing.
- Create `tests/test_chatgpt.py`: SDK request, streaming, history, retry, lifecycle, and quota tests.
- Create `core/chatgpt_conversation.py`: `ExternalConversationController` adapter for the ChatGPT backend.
- Modify `main.py`, `core/app.py`, `core/wakeup_session.py`, startup scripts, and adjacent tests: CLI commands, enable flag, application lifecycle, and wake routing.
- Modify `config.py`, `.gitignore`, and `README.md`: final configuration and operator documentation.
- Modify `pyproject.toml` and `uv.lock`: add PyJWT with cryptographic verification support.

### Task 1: Restore the OpenAI-compatible backend to API-key-only operation

**Files:**
- Modify: `core/openai.py`
- Modify: `tests/test_openai.py`
- Delete: `core/oauth_device.py`
- Delete: `tests/test_oauth_device.py`

**Interfaces:**
- Produces: `OpenAIManager` using `AsyncOpenAI(api_key, base_url, timeout, default_headers)` and `chat.completions.create()`
- Preserves: custom base URLs, complete `/chat/completions` URL normalization, history, session headers, request fields, response extraction, and TTS routing
- Removes: `auth_type`, `OAuthDeviceConfig`, and `DeviceOAuthTokenManager`

- [ ] **Step 1: Replace OAuth-mode tests with failing API-key-only contract tests**

Add tests named `test_openai_config_ignores_no_oauth_mode`, `test_empty_api_key_supports_unauthenticated_compatible_service`, and retain the existing API-key SDK/request mapping tests. Assert `OpenAIManager` exposes no OAuth manager/config state and never retries an `AuthenticationError` at the application layer.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_openai.py`

Expected: FAIL while OAuth-specific state and retry behavior still exist.

- [ ] **Step 3: Remove generic OAuth and restore the single API-key SDK path**

Remove OAuth imports, configuration parsing, token-manager lifecycle, and OAuth retry. Keep the long-lived SDK client, empty-key callable, normalized compatible base URL, and existing Chat Completions mapping. Delete the obsolete OAuth module and tests.

- [ ] **Step 4: Run OpenAI and adjacent TTS tests**

Run: `.venv/bin/python -m pytest -q tests/test_openai.py tests/test_openai_tts.py tests/test_tts_router.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add core/openai.py tests/test_openai.py core/oauth_device.py tests/test_oauth_device.py
git commit -m "refactor: separate OAuth from compatible OpenAI chat"
```

### Task 2: SIWC configuration and protected credential storage

**Files:**
- Create: `core/chatgpt_auth.py`
- Create: `tests/test_chatgpt_auth.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ChatGPTAuthConfig.from_mapping(config: Mapping[str, Any]) -> ChatGPTAuthConfig`
- Produces: `ChatGPTCredentials.from_mapping(data: Mapping[str, Any]) -> ChatGPTCredentials`
- Produces: `ChatGPTCredentialStore(path: str)` with `load()`, `save(credentials)`, `delete()`, and `quarantine()`
- Produces: `ChatGPTAuthError(RuntimeError)` with redacted messages

- [ ] **Step 1: Write failing config and storage tests**

Add tests named `test_config_uses_fixed_official_endpoints_and_defaults`, `test_config_rejects_non_loopback_callback`, `test_credentials_require_issued_client_tokens_scope_and_identity`, `test_store_writes_atomically_with_mode_0600`, `test_corrupt_store_is_quarantined`, and `test_default_credential_variants_are_gitignored`. Pin default port `1455`, path `/auth/callback`, file `./data/chatgpt-siwc.json`, issuer `https://auth.openai.com`, and resource/base URL `https://api.openai.com/v1`.

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_auth.py`

Expected: FAIL because `core.chatgpt_auth` does not exist.

- [ ] **Step 3: Add JWT dependency and implement config, credential, and store types**

Run: `uv add 'PyJWT[crypto]>=2.10,<3'`.

Use immutable dataclasses. Store the issued client ID, opaque host ID, verified issuer/subject/email, ID/access/refresh tokens, token type, expiry, scopes, and selected model. Redact secret fields from `repr`. Add `data/chatgpt-siwc.json*` to `.gitignore`.
Remove the obsolete `data/openai-oauth-token.json*` ignore rule in the same hunk.

- [ ] **Step 4: Verify Task 2**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_auth.py && uv lock --check`

Expected: PASS and lock file current.

- [ ] **Step 5: Commit Task 2**

```bash
git add core/chatgpt_auth.py tests/test_chatgpt_auth.py pyproject.toml uv.lock .gitignore
git commit -m "feat: define protected ChatGPT SIWC credentials"
```

### Task 3: PKCE login, loopback callback, and OIDC validation

**Files:**
- Modify: `core/chatgpt_auth.py`
- Modify: `tests/test_chatgpt_auth.py`

**Interfaces:**
- Produces: `ChatGPTAuthManager(config: ChatGPTAuthConfig)`
- Produces: `async login(url_presenter: Callable[[str], None], model_selector: Callable[[Sequence[ChatGPTModel]], str]) -> ChatGPTCredentials`
- Produces private boundaries: `_build_authorization_url()`, `_await_callback()`, `_exchange_code()`, `_fetch_jwks()`, and `_validate_id_token()`

- [ ] **Step 1: Write failing authorization and callback tests**

Add tests named `test_first_login_uses_dynamic_client_pkce_and_exact_official_parameters`, `test_callback_binds_loopback_exact_path_and_validates_state`, `test_denial_timeout_wrong_path_and_wrong_state_preserve_old_credentials`, `test_reauthorization_reuses_client_and_redacts_id_token_hint_url`, and `test_reauthorization_rejects_changed_callback_client_id`. Assert the same exact redirect URI is used in authorization and exchange.

- [ ] **Step 2: Write failing token and ID Token validation tests**

Add tests named `test_exchange_uses_issued_client_without_secret`, `test_id_token_verifies_jwks_issuer_audience_expiry_and_nonce`, `test_missing_direct_usage_scope_is_rejected`, and one subtest per invalid signature/issuer/audience/expiry/nonce. Assert no credential save occurs on any failure.

- [ ] **Step 3: Run login tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_auth.py -k 'login or callback or reauthorization or exchange or id_token or scope'`

Expected: FAIL because the login protocol is not implemented.

- [ ] **Step 4: Implement the official authorization-code flow**

Use `aiohttp.web` for a one-attempt loopback listener and `secrets` plus SHA-256 for state, nonce, and PKCE. Use the callback's issued client ID only for first registration. Verify RS256 ID Tokens with the fetched OpenAI JWKS through PyJWT and activate credentials only after all identity and scope checks pass. Always close the listener and HTTP session on cancellation or shutdown.

- [ ] **Step 5: Run all auth tests**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_auth.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add core/chatgpt_auth.py tests/test_chatgpt_auth.py
git commit -m "feat: implement official ChatGPT SIWC login"
```

### Task 4: Model discovery, refresh, logout, and administrative CLI

**Files:**
- Modify: `core/chatgpt_auth.py`
- Modify: `tests/test_chatgpt_auth.py`
- Modify: `main.py`
- Create: `tests/test_chatgpt_cli.py`

**Interfaces:**
- Produces: `async ChatGPTAuthManager.get_access_token() -> str`
- Produces: `async ChatGPTAuthManager.list_models() -> list[ChatGPTModel]`
- Produces: `async ChatGPTAuthManager.logout() -> LogoutResult`
- Produces: `invalidate_access_token() -> None` and `async close() -> None`
- Produces CLI commands: `chatgpt-login`, `chatgpt-logout`, and `chatgpt-smoke-test`

- [ ] **Step 1: Write failing refresh, catalog, and logout tests**

Add `test_valid_access_token_skips_refresh`, `test_near_expiry_refresh_rotates_and_persists_tokens`, `test_concurrent_first_call_after_restart_shares_one_refresh`, `test_refresh_failure_preserves_still_valid_credentials`, `test_invalid_grant_requires_login_without_deleting_diagnostics`, `test_model_catalog_keeps_visible_order`, `test_configured_or_selected_model_must_be_visible`, `test_logout_revokes_then_deletes`, and `test_logout_network_failure_deletes_locally_and_reports_unconfirmed`.

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_auth.py -k 'refresh or concurrent or model or logout'`

Expected: FAIL on missing lifecycle methods.

- [ ] **Step 3: Implement serialized refresh, models, revocation, and shutdown**

Use one lock and one shared in-flight task for rotating refresh tokens. Parse the SIWC model response's `models` array and keep entries with `visibility == "list"` as `ChatGPTModel(slug, display_name)`. Revoke through the discovered official revocation endpoint before local deletion when possible.

- [ ] **Step 4: Write failing CLI tests**

In `tests/test_chatgpt_cli.py`, add `test_login_command_prints_ssh_hint_and_selects_model`, `test_logout_command_reports_revocation_state`, `test_smoke_command_requires_credentials`, and `test_service_start_does_not_run_for_admin_commands`. Patch network, terminal input, and auth manager boundaries.

- [ ] **Step 5: Implement administrative subcommands in `main.py`**

Refactor parsing to return a command without starting audio services. `chatgpt-login` prints the port-forward hint and authorization URL only to the interactive terminal, prompts for a numbered visible model, and saves it. `chatgpt-smoke-test` calls the ChatGPT manager with a fixed short prompt and requires a completed response.

- [ ] **Step 6: Verify Task 4**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_auth.py tests/test_chatgpt_cli.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add core/chatgpt_auth.py tests/test_chatgpt_auth.py main.py tests/test_chatgpt_cli.py
git commit -m "feat: manage ChatGPT login lifecycle"
```

### Task 5: Tool-free Responses streaming and wake-session history

**Files:**
- Create: `core/chatgpt.py`
- Create: `tests/test_chatgpt.py`

**Interfaces:**
- Consumes: `ChatGPTAuthManager.get_access_token()`, `invalidate_access_token()`, and selected/visible model validation from Task 4
- Produces: `ChatGPTManager.initialize_from_config()`, `connect()`, `close()`, `reset_session()`, `send()`, `_send_and_track()`, `_wait_response()`, and `send_and_play_reply()`
- Produces: `_request_response(text: str) -> str | None`

- [ ] **Step 1: Write failing SDK and exact request tests**

Add `test_client_uses_async_oauth_credential_official_base_and_zero_retries`, `test_response_request_sends_full_history_instructions_store_false_stream_true`, and `test_request_omits_tools_and_all_siwc_unsupported_fields`. Assert no API key, custom base URL, `temperature`, `max_output_tokens`, `previous_response_id`, `conversation`, or tool fields are accepted from config.

- [ ] **Step 2: Write failing stream and history tests**

Add `test_completed_stream_appends_turn_and_returns_joined_text`, `test_text_then_failed_does_not_append_or_return_partial`, `test_text_then_eof_without_completed_fails`, `test_incomplete_stream_fails`, `test_new_wake_reset_clears_history`, and `test_quota_errors_become_short_user_message`. Use fake async SDK events.

- [ ] **Step 3: Write failing lifecycle and retry tests**

Add `test_first_authentication_error_invalidates_and_retries_once`, `test_second_authentication_error_escapes`, `test_concurrent_requests_reuse_one_client`, and `test_close_cancels_waiters_and_closes_sdk_and_auth`.

- [ ] **Step 4: Run ChatGPT manager tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt.py`

Expected: FAIL because `core.chatgpt` does not exist.

- [ ] **Step 5: Implement the ChatGPT manager**

Follow the existing manager's send/tracking/TTS public shape, but keep one wake-session history list and stream `responses.create`. Buffer deltas privately and publish text only after `response.completed`. Give quota failures a safe speech-length message and keep raw redacted error context in `last_error`.

- [ ] **Step 6: Verify Task 5**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt.py tests/test_tts_router.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add core/chatgpt.py tests/test_chatgpt.py
git commit -m "feat: stream tool-free ChatGPT responses"
```

### Task 6: Application lifecycle, wake routing, and conversation controller

**Files:**
- Create: `core/chatgpt_conversation.py`
- Modify: `core/app.py`
- Modify: `core/wakeup_session.py`
- Modify: `main.py`
- Modify: `core/services/audio/kws/keywords.py`
- Modify: `scripts/start.sh`
- Modify: `Dockerfile`
- Modify: `tests/test_wakeup_interruption.py`
- Modify: `tests/test_wakeup_keywords.py`
- Create: `tests/test_chatgpt_routing.py`

**Interfaces:**
- Consumes: `ChatGPTManager` public lifecycle and send interfaces from Task 5
- Produces: `ChatGPTConversationController(ExternalConversationController)` with prefix/source `chatgpt`
- Produces: `MainApp` methods `send_to_chatgpt()` and `send_to_chatgpt_and_play_reply()`; no session-key setter because ChatGPT history is scoped to the active wake only
- Produces: wake routing for `before_wakeup -> "chatgpt"`

- [ ] **Step 1: Write failing dual-backend flag and lifecycle tests**

Add tests asserting `CHATGPT_ENABLE` is independent in `main.py`, keyword preparation, `scripts/start.sh`, and `Dockerfile`; both enabled backends initialize and close their own managers exactly once; admin commands still bypass services.

- [ ] **Step 2: Write failing routing and interruption tests**

Add `test_chatgpt_route_starts_dedicated_controller_with_fresh_history`, `test_chatgpt_controller_uses_chatgpt_config_and_source`, `test_native_asr_dispatch_includes_chatgpt_controller`, `test_interrupt_cancels_chatgpt_without_touching_openai_state`, and `test_reset_waits_for_openai_and_chatgpt_cleanup_independently`.

- [ ] **Step 3: Run routing tests and verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_routing.py tests/test_wakeup_interruption.py tests/test_wakeup_keywords.py`

Expected: FAIL because ChatGPT routing does not exist.

- [ ] **Step 4: Integrate the dedicated backend**

Add `enable_chatgpt` through argument parsing and `MainApp`, initialize and close `ChatGPTManager`, add the controller/task pair to wake dispatch/reset/interrupt/native-ASR consumption, and reset ChatGPT history immediately before each new `"chatgpt"` conversation. Extend keyword startup conditions with `CHATGPT_ENABLE`.

- [ ] **Step 5: Run application and adjacent conversation tests**

Run: `.venv/bin/python -m pytest -q tests/test_chatgpt_routing.py tests/test_wakeup_interruption.py tests/test_wakeup_keywords.py tests/test_native_kws_takeover.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add core/chatgpt_conversation.py core/app.py core/wakeup_session.py main.py core/services/audio/kws/keywords.py scripts/start.sh Dockerfile tests/test_chatgpt_routing.py tests/test_wakeup_interruption.py tests/test_wakeup_keywords.py
git commit -m "feat: route wake sessions to ChatGPT"
```

### Task 7: Configuration, documentation, regression, and live acceptance

**Files:**
- Modify: `config.py`
- Modify: `README.md`
- Modify: `tests/test_openai.py`
- Modify: `tests/test_chatgpt_auth.py`
- Modify: `tests/test_chatgpt.py`
- Modify: `tests/test_chatgpt_routing.py`

**Interfaces:**
- Consumes: all configuration names and commands from Tasks 1-6
- Produces: operator instructions and a real smoke-test procedure matching the implementation

- [ ] **Step 1: Add final configuration/documentation contract tests**

Assert the default `openai` block contains API-key compatible fields and no OAuth block; the `chatgpt` block contains callback port `1455`, credential file, blank optional model, history, timeout, and TTS fields but no API key/base URL/tool fields; README/config contain no platform-specific server name or token values.

- [ ] **Step 2: Run contract tests and verify the config gap**

Run: `.venv/bin/python -m pytest -q tests/test_openai.py tests/test_chatgpt.py tests/test_chatgpt_auth.py tests/test_chatgpt_routing.py`

Expected: FAIL until the default config and documentation match the implemented contract.

- [ ] **Step 3: Update `config.py` without staging unrelated ASR/VAD edits**

Remove `auth_type` and `oauth` from `openai`; add the dedicated `chatgpt` block from the spec. Update example wake routing with `"chatgpt"` while preserving existing native-wake behavior and avoiding an extra “我在” response. Stage only these hunks with `git add -p config.py`.

- [ ] **Step 4: Update README operations and recovery guidance**

Document the two independent backends, `CHATGPT_ENABLE`, SSH `-L` example, login/logout/smoke commands, callback security, model selection, protected credential path, refresh/relogin, plan-limit behavior, no tools, per-wake history, and separate TTS authentication. Remove the obsolete generic Device OAuth instructions.

- [ ] **Step 5: Run focused and full automated verification**

Run: `.venv/bin/python -m pytest -q tests/test_openai.py tests/test_chatgpt_auth.py tests/test_chatgpt_cli.py tests/test_chatgpt.py tests/test_chatgpt_routing.py tests/test_openai_tts.py tests/test_tts_router.py`

Run: `.venv/bin/python -m pytest -q`

Run: `uv lock --check && git diff --check`

Expected: all tests pass, lock current, no whitespace errors.

- [ ] **Step 6: Commit Task 7 automated work**

```bash
git add -p config.py
git add README.md tests/test_openai.py tests/test_chatgpt.py tests/test_chatgpt_auth.py tests/test_chatgpt_routing.py
git commit -m "docs: configure ChatGPT plan sign-in"
```

- [ ] **Step 7: Perform real SIWC acceptance with the user**

User establishes the documented SSH tunnel. Run `python main.py chatgpt-login`, complete browser authorization, select an account-visible model, then run `python main.py chatgpt-smoke-test`. Require `response.completed` and the expected short text. Restart the process and repeat the smoke test to prove persisted credentials and model selection.

Expected: both live requests succeed. Do not claim real ChatGPT connectivity before this step passes.

- [ ] **Step 8: Record acceptance without committing credentials**

Verify `git status --short` does not show the credential file or token variants. Record only redacted command outcomes in the execution ledger; never add credentials, authorization URLs, account email, or tokens to Git.
