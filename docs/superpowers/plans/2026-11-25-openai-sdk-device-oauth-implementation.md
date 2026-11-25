# OpenAI SDK Device OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chat backend's direct HTTP call with `AsyncOpenAI` and add persistent RFC 8628 Device Authorization Flow authentication while preserving API key compatibility.

**Architecture:** `OpenAIManager` owns conversation state and one long-lived `AsyncOpenAI` client. A focused `DeviceOAuthTokenManager` owns device authorization, token refresh, persistence, and concurrency, and exposes an asynchronous credential callable to the SDK.

**Tech Stack:** Python 3.12+, `openai>=2.34,<3`, `aiohttp`, `asyncio`, `unittest`/`pytest`

**Spec:** `docs/superpowers/specs/2026-11-24-openai-sdk-device-oauth-design.md`

## Global Constraints

- Keep `openai>=2.34,<3`; do not migrate to the Responses API.
- Preserve API key mode, custom OpenAI-compatible base URLs, Chat Completions, session headers, history, temperature, `max_tokens`, `extra_body`, and TTS routing.
- OAuth modes are exactly `api_key` and `oauth_device`; OAuth applies only to chat, not TTS.
- Persist OAuth tokens atomically with file mode `0600`; never log client secrets or token values.
- Device flow uses RFC 8628 polling and supports optional `audience` and `OPENAI_OAUTH_CLIENT_SECRET`.
- Do not include or overwrite unrelated working-tree changes, especially the existing ASR/VAD edits in `config.py`.

## Review Focus

- Missing or malformed OAuth endpoints/client ID must fail with a specific configuration error before any network request; Task 1 pins this.
- Empty `audience` and empty client secret must be omitted from form data rather than sent as empty strings; Task 2 pins this.
- Token responses with missing/invalid `access_token` or `expires_in` must fail safely without corrupting the stored token; Task 2 pins this.
- A refresh response that omits `refresh_token` must retain the previous refresh token, while a rotated value must replace it; Task 3 pins this.
- Shutdown during device polling must cancel the poll and unblock all waiters without leaking a client session; Task 3 pins this.

---

## File Structure

- Create `core/oauth_device.py`: OAuth configuration, token state, RFC 8628 protocol, persistence, concurrency, and lifecycle.
- Create `tests/test_oauth_device.py`: isolated async tests for device authorization, refresh, storage, concurrency, and shutdown.
- Modify `core/openai.py`: construct and reuse `AsyncOpenAI`, choose authentication mode, invoke Chat Completions, retry one authentication failure, and close resources.
- Modify `tests/test_openai.py`: SDK construction, request mapping, response extraction, retry, reload, and shutdown tests.
- Modify `pyproject.toml` and `uv.lock`: add and lock the official OpenAI Python SDK.
- Modify `config.py` and `README.md`: document API key and Device OAuth configuration and operation without changing unrelated settings.

### Task 1: SDK dependency and OAuth configuration contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `core/oauth_device.py`
- Create: `tests/test_oauth_device.py`

**Interfaces:**
- Produces: `OAuthDeviceConfig.from_mapping(config: Mapping[str, Any]) -> OAuthDeviceConfig`
- Produces: `OAuthDeviceConfigError(ValueError)`
- Produces fields: `device_authorization_endpoint`, `token_endpoint`, `client_id`, `scope`, `audience`, `token_file`, `refresh_leeway`, `client_secret`

- [ ] **Step 1: Write failing configuration tests**

Add tests named `test_config_parses_required_and_optional_fields`, `test_config_reads_client_secret_from_environment`, and `test_config_rejects_missing_endpoint_or_client_id`. Assert the exact defaults `token_file="./data/openai-oauth-token.json"` and `refresh_leeway=120`, and assert invalid input raises `OAuthDeviceConfigError` before constructing an HTTP session.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest -q tests/test_oauth_device.py`

Expected: FAIL because `core.oauth_device` does not exist.

- [ ] **Step 3: Add the SDK dependency and minimal configuration types**

Run: `uv add 'openai>=2.34,<3'`

Implement the interfaces above in `core/oauth_device.py`. Normalize strings, require absolute HTTP(S) endpoint URLs, and never copy a client secret into `repr` output.

- [ ] **Step 4: Run configuration tests and dependency validation**

Run: `pytest -q tests/test_oauth_device.py && uv lock --check`

Expected: all focused tests pass and the lock file is current.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add pyproject.toml uv.lock core/oauth_device.py tests/test_oauth_device.py
git commit -m "feat: define OpenAI device OAuth configuration"
```

### Task 2: RFC 8628 authorization and refresh protocol

**Files:**
- Modify: `core/oauth_device.py`
- Modify: `tests/test_oauth_device.py`

**Interfaces:**
- Consumes: `OAuthDeviceConfig`
- Produces: `DeviceOAuthTokenManager(config: OAuthDeviceConfig)`
- Produces: `async get_access_token() -> str`, `invalidate_access_token() -> None`, `async close() -> None`
- Produces: `OAuthDeviceAuthorizationError(RuntimeError)` with safe, token-free messages

- [ ] **Step 1: Write failing protocol tests**

Add async tests for:

- `test_device_flow_logs_code_and_polls_until_success`
- `test_authorization_pending_uses_server_interval`
- `test_slow_down_adds_five_seconds_to_interval`
- `test_access_denied_fails_without_retry`
- `test_expired_device_code_starts_one_new_authorization`
- `test_empty_audience_and_secret_are_omitted`
- `test_invalid_token_response_does_not_replace_current_token`

Use a fake `aiohttp` session and patched async sleep. Assert device requests use `client_id`, optional `scope`/`audience`, token polls use the device-code grant type, and logs contain the verification URL and user code but no token value.

- [ ] **Step 2: Run protocol tests and verify failure**

Run: `pytest -q tests/test_oauth_device.py -k 'device_flow or authorization_pending or slow_down or access_denied or expired_device or empty_audience or invalid_token'`

Expected: FAIL because `DeviceOAuthTokenManager` is incomplete.

- [ ] **Step 3: Implement device authorization and refresh acquisition**

Implement the public interfaces plus private coroutines `_request_device_code()`, `_poll_device_token(device: Mapping[str, Any])`, `_refresh_access_token()`, and `_post_form(url: str, data: Mapping[str, str])`. Parse `expires_in` into an absolute expiry using `time.time()`, follow RFC 8628 error behavior, and redact response bodies from raised/logged errors.

- [ ] **Step 4: Run protocol tests**

Run: `pytest -q tests/test_oauth_device.py`

Expected: all current OAuth tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add core/oauth_device.py tests/test_oauth_device.py
git commit -m "feat: implement device OAuth protocol"
```

### Task 3: Secure token persistence, refresh, concurrency, and shutdown

**Files:**
- Modify: `core/oauth_device.py`
- Modify: `tests/test_oauth_device.py`

**Interfaces:**
- Consumes: `DeviceOAuthTokenManager.get_access_token()` from Task 2
- Produces: atomic token loading/saving behind the same public manager interface
- Produces: exactly one shared authorization or refresh operation for concurrent callers

- [ ] **Step 1: Write failing state and lifecycle tests**

Add async tests named:

- `test_valid_persisted_token_skips_network`
- `test_token_file_is_atomically_written_with_mode_0600`
- `test_corrupt_token_file_is_moved_to_invalid_suffix`
- `test_refresh_happens_within_leeway`
- `test_refresh_without_new_refresh_token_retains_old_value`
- `test_rotating_refresh_token_replaces_old_value`
- `test_refresh_outage_uses_still_valid_access_token`
- `test_concurrent_callers_share_one_refresh`
- `test_close_cancels_poll_unblocks_waiters_and_closes_session`

- [ ] **Step 2: Run state tests and verify failure**

Run: `pytest -q tests/test_oauth_device.py -k 'persisted or token_file or corrupt or refresh or concurrent or close'`

Expected: FAIL on missing persistence/concurrency behavior.

- [ ] **Step 3: Implement state management**

Add private methods `_load_token()`, `_save_token(token: Mapping[str, Any])`, `_is_access_token_valid(leeway: int = 0) -> bool`, and `_invalidate_stored_access_token()`. Use one `asyncio.Lock`, one tracked in-flight task, same-directory temporary replacement, explicit `chmod(0o600)`, and a tracked lazy `aiohttp.ClientSession` closed by `close()`.

- [ ] **Step 4: Run all OAuth manager tests**

Run: `pytest -q tests/test_oauth_device.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add core/oauth_device.py tests/test_oauth_device.py
git commit -m "feat: persist and refresh OAuth tokens safely"
```

### Task 4: OpenAI SDK chat integration and resource lifecycle

**Files:**
- Modify: `core/openai.py`
- Modify: `tests/test_openai.py`

**Interfaces:**
- Consumes: `DeviceOAuthTokenManager` and its async `get_access_token()` callable
- Produces: `OpenAIManager._ensure_client() -> AsyncOpenAI`
- Produces: `OpenAIManager._close_client() -> None` as an async class method
- Preserves: `OpenAIManager._request_chat_completion(text: str) -> str | None`

- [ ] **Step 1: Replace header-only tests with failing SDK construction tests**

Add tests asserting:

- `api_key` mode passes the configured/runtime key, base URL, timeout, and session header to one reused `AsyncOpenAI` client.
- `oauth_device` mode creates one token manager and passes its async credential callable to `AsyncOpenAI`.
- a configuration fingerprint change causes the next async request to close old resources before constructing replacements.
- `close()` cancels response waiters and closes both SDK and OAuth clients.

Retain the existing environment precedence tests.

- [ ] **Step 2: Run client construction tests and verify failure**

Run: `pytest -q tests/test_openai.py -k 'client or oauth or close or runtime_api_key or config_api_key'`

Expected: FAIL because the manager still sends direct `aiohttp` requests.

- [ ] **Step 3: Implement SDK construction and lazy reload cleanup**

Remove chat-path `aiohttp` usage from `core/openai.py`. Parse `auth_type` and nested OAuth config during reload, mark the existing client stale when its configuration fingerprint changes, and perform async cleanup in `_ensure_client()` on the application loop. Use `default_headers` only for the optional session header; let the SDK own `Authorization`.

- [ ] **Step 4: Write failing Chat Completions mapping and retry tests**

Add async tests asserting:

- `client.chat.completions.create()` receives `model`, `messages`, `stream=False`, optional temperature, `max_tokens`, and `extra_body` without losing unknown compatible fields.
- string and structured message content are converted to the same text returned by the old implementation and successful turns update history.
- the first SDK `AuthenticationError` invalidates the OAuth access token and retries exactly once.
- a second `AuthenticationError` is raised without a loop; API key mode does not trigger OAuth invalidation.

- [ ] **Step 5: Run mapping tests and verify failure**

Run: `pytest -q tests/test_openai.py -k 'completion or structured or authentication or history'`

Expected: FAIL until the SDK request path is complete.

- [ ] **Step 6: Implement Chat Completions and one authentication retry**

Call `await client.chat.completions.create(...)`, convert SDK response models with `model_dump()` before passing them to the existing extraction helper, and retry only the OAuth authentication case once after invalidation. Keep the public send/play behavior unchanged.

- [ ] **Step 7: Run OpenAI and adjacent TTS tests**

Run: `pytest -q tests/test_openai.py tests/test_openai_tts.py tests/test_tts_router.py`

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

```bash
git add core/openai.py tests/test_openai.py
git commit -m "feat: use OpenAI SDK with device OAuth"
```

### Task 5: Configuration examples, README, and full regression

**Files:**
- Modify: `config.py`
- Modify: `README.md`
- Modify if needed: `tests/test_openai.py`
- Modify if needed: `tests/test_oauth_device.py`

**Interfaces:**
- Consumes: configuration contract and runtime behavior from Tasks 1-4
- Produces: user-facing setup and recovery instructions matching implemented names and defaults

- [ ] **Step 1: Add final configuration-contract assertions**

Assert the documented default API key configuration still initializes, the complete Device OAuth example parses, an unsupported `auth_type` raises a clear error, and no default/example contains a client secret or platform-specific server name.

- [ ] **Step 2: Run the contract tests and verify any documentation/config gap**

Run: `pytest -q tests/test_openai.py tests/test_oauth_device.py`

Expected: PASS for runtime behavior; any new config assertion fails until the example is updated.

- [ ] **Step 3: Update configuration and README**

Add `auth_type` and the nested `oauth` block to `config.py` without altering unrelated user changes. Update the OpenAI-compatible service README section with API key and Device OAuth examples, console verification steps, token path/permissions, automatic refresh, safe forced reauthorization, and the explicit statement that OpenAI-compatible TTS OAuth is not included.

- [ ] **Step 4: Run focused and full verification**

Run: `pytest -q tests/test_oauth_device.py tests/test_openai.py tests/test_openai_tts.py tests/test_tts_router.py`

Run: `pytest -q`

Run: `git diff --check`

Expected: all tests pass and no whitespace errors are reported.

- [ ] **Step 5: Inspect the final diff for scope and secret safety**

Run: `git diff -- pyproject.toml uv.lock core/oauth_device.py core/openai.py tests/test_oauth_device.py tests/test_openai.py config.py README.md`

Expected: only the planned SDK/OAuth changes plus the user's pre-existing `config.py` edits are present; no token, client secret, or platform-specific server label appears.

- [ ] **Step 6: Commit documentation and examples without staging unrelated edits**

Stage only the OAuth hunks in `config.py` and the complete planned README/test changes, then commit:

```bash
git add README.md tests/test_openai.py tests/test_oauth_device.py
git add -p config.py
git commit -m "docs: explain OpenAI device authorization"
```

Do not stage the pre-existing ASR/VAD hunks in `config.py`.
