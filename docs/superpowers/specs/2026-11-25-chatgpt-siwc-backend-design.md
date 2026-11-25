# ChatGPT SIWC Voice Backend Design

## Goal

Add a dedicated ChatGPT backend that uses the official Sign in with ChatGPT
(SIWC) flow and the user's eligible ChatGPT plan for ordinary voice questions.
Keep the existing OpenAI-compatible backend for API-key services such as
DeepSeek, Ollama, and LM Studio.

The ChatGPT backend is conversation-only. It must not expose shell, filesystem,
MCP, web, computer-use, or other agent tools.

## Product boundaries

The two chat backends are independent:

- `openai` continues to use OpenAI-compatible Chat Completions and API-key
  authentication. It keeps custom `base_url` support for services such as
  DeepSeek.
- `chatgpt` uses SIWC credentials and the official OpenAI Responses API at
  `https://api.openai.com/v1`. It does not accept API keys or custom base URLs.
- Wake routing returns `"openai"` for the compatible backend and `"chatgpt"`
  for the SIWC backend.
- `OPENAI_ENABLE` and `CHATGPT_ENABLE` enable the two backends independently.
- Conversation history, selected model, prompts, errors, and lifecycle state
  are not shared between the two backends.
- TTS remains a separate shared playback layer. This design does not add SIWC
  authentication to TTS.

The generic self-hosted Device OAuth implementation is removed. It is neither
an OpenAI-compatible API-key feature nor the official SIWC protocol.

## Approaches considered

### Embedded SIWC with an SSH-forwarded loopback callback

This is the selected approach. The bridge implements the official public-client
authorization-code flow with PKCE, listens only on a loopback callback, owns its
SIWC token lifecycle, and calls the Responses API through the official OpenAI
Python SDK. An SSH local-port forward makes the server callback reachable from
the user's local browser.

This approach gives the bridge a direct, tool-free inference path and avoids a
Codex runtime dependency.

### Codex App Server

Codex App Server can manage ChatGPT sign-in and token refresh, but credentials
remain inside App Server and all requests go through its RPC and agent model.
That extra agent layer is unnecessary for ordinary voice questions and makes
the no-tools boundary harder to reason about.

### Local login helper plus credential import

A separate local helper could complete the loopback flow and transfer protected
credentials to the server. It works for remote machines but adds a second
binary, a credential export format, and another operational step. SSH port
forwarding provides the required callback path without those components.

## Configuration and commands

The dedicated configuration has this shape:

```python
"chatgpt": {
    "callback_port": 1455,
    "credential_file": "./data/chatgpt-siwc.json",
    "model": "",
    "system_prompt": "",
    "history_max_messages": 20,
    "response_timeout": 120,
    "tts_provider": None,
    "tts_speaker": "xiaoai",
    "tts_speed": 1.0,
    "exit_keywords": ["退出", "停止", "再见"],
}
```

`model` is an optional explicit override. When it is empty, the backend uses
the account-specific model selected during login. An unavailable configured
model is an error; the bridge never silently substitutes another model.

Two administrative commands manage the account:

- `chatgpt-login` performs first registration or reauthorization, validates the
  result, lists the account's visible models, prompts for one selection, and
  saves the credentials and selected model.
- `chatgpt-logout` attempts to revoke the renewable session and then removes
  local credentials. If remote revocation cannot be confirmed, it still clears
  the local credentials and reports that distinction.

The first version manages one active ChatGPT account. Switching accounts is an
explicit logout followed by a new login.

## Login flow

Before running `chatgpt-login`, the user forwards the configured port from the
browser machine to the bridge server, for example:

```bash
ssh -L 1455:127.0.0.1:1455 user@bridge-server
```

The command then:

1. Loads or creates a persistent opaque `ext_agent_host_id` for this server.
2. Binds a temporary HTTP callback listener to
   `127.0.0.1:<callback_port>/auth/callback`.
3. Generates fresh high-entropy `state`, OIDC `nonce`, and PKCE verifier and
   challenge.
4. Prints an authorization URL for
   `https://auth.openai.com/api/accounts/authorize` using
   `client_id=dynamic_agent_client` for first registration, or the saved issued
   client ID for reauthorization.
5. Requests `openid profile email offline_access resource.invoke
   chatgpt.tokens.use.direct` for resource `https://api.openai.com/v1`.
6. Validates callback state and obtains the issued dynamic client ID.
7. Exchanges the authorization code at the official token endpoint with the
   same redirect URI, resource, and PKCE verifier. No client secret is used.
8. Verifies the ID Token signature against OpenAI JWKS and validates issuer,
   audience, expiration, and nonce.
9. Requires the granted `chatgpt.tokens.use.direct` scope before making the new
   credentials active.
10. Atomically saves the validated account, issued client ID, host ID, tokens,
    scopes, and expiry metadata.
11. Fetches the account-specific model catalog, displays entries whose
    visibility is `list`, and saves the user's selection.

The command never replaces working credentials until every validation step has
succeeded. Login has a bounded timeout and the callback listener is closed on
success, failure, cancellation, or shutdown.

## Credential lifecycle

The default credential file is `./data/chatgpt-siwc.json`. It and quarantined or
backup variants are ignored by Git. Writes use a same-directory temporary file,
atomic replacement, and owner-only mode `0600`.

The credential record includes the issued client ID, opaque host ID, validated
account identity, retained ID Token, access token, rotating refresh token,
granted scopes, access expiry, and selected model. Tokens, authorization codes,
PKCE verifiers, and authorization URLs containing an `id_token_hint` are never
logged.

The access token is refreshed shortly before its one-hour expiry. Refreshes for
one session are serialized so concurrent voice requests cannot race a rotating
refresh token. A successful response atomically replaces both access and refresh
tokens. A first authentication failure triggers one refresh and one request
retry; a second failure is reported without a loop.

An invalid or expired refresh token, revoked grant, missing SIWC scope, or
mismatched account requires `chatgpt-login` again. Existing valid credentials
are not deleted because of a transient endpoint failure.

## Inference and conversation flow

The backend creates an `AsyncOpenAI` client with the current OAuth access token,
the official base URL, and SDK retries disabled. Each inference uses
`client.responses.create()` with:

- the selected account-visible model;
- the full current wake-session history as `input`;
- an optional `instructions` string instead of a system-role input item;
- `store=False`;
- `stream=True`;
- no tools or tool-choice fields.

SIWC-preview-incompatible parameters, including `temperature`,
`max_output_tokens`, `previous_response_id`, and persistent conversation IDs,
are not sent. The backend consumes text deltas but treats a turn as successful
only after `response.completed`. It rejects `response.failed`,
`response.incomplete`, malformed streams, and streams that terminate without a
completion event. Failed or partial turns do not enter history and are not sent
to TTS.

History exists only for one wake session. Multiple turns during the same wake
session share context. Exiting that session discards its history, so the next
wake starts a new conversation.

The backend recognizes subscription-sharing quota errors and reports a concise
user-facing message. It does not fall back to an API key or another model.

## Security boundaries

- The SIWC backend is fixed to official OpenAI authorization, JWKS, token,
  model-list, and Responses endpoints.
- The callback listener binds only to `127.0.0.1` and accepts only the exact
  callback path for the current attempt.
- State, nonce, PKCE, issued-client-ID, account identity, scope, issuer,
  audience, and token-expiry checks fail closed.
- No shell, filesystem, web, MCP, computer-use, hosted tools, custom tools, or
  child agents are exposed to the model.
- No ChatGPT conversation or memory access is requested or assumed.
- The bridge shows that connected-app usage consumes the user's existing plan
  allowance and can be managed in ChatGPT settings.

## Error handling

- Callback denial, state mismatch, timeout, or malformed parameters aborts the
  pending login without changing saved credentials.
- Token or JWKS network failures produce redacted errors and leave valid saved
  credentials intact.
- Model-catalog failure leaves login credentials valid but does not activate an
  unvalidated model selection.
- An unavailable saved or configured model prints the current visible model
  slugs and requires an explicit selection.
- `subscription_sharing_usage_limit_exceeded` and
  `subscription_sharing_usage_unavailable` become short quota messages suitable
  for speech.
- Shutdown cancels login, refresh, and inference tasks and closes callback,
  HTTP, SDK, and TTS resources.

## Testing and acceptance

Automated tests cover:

- authorization URL construction and secret redaction;
- PKCE, state, nonce, callback-path, and timeout behavior;
- issued dynamic-client-ID handling and token exchange;
- JWKS signature, issuer, audience, expiry, nonce, and scope validation;
- atomic `0600` credential storage and Git ignore rules;
- proactive refresh, rotating refresh tokens, concurrent callers, transient
  failures, invalid grants, and one authentication retry;
- account-visible model filtering, selection, override validation, and missing
  model errors;
- exact Responses API request fields and explicit absence of unsupported fields
  and tools;
- streaming completion, failure, incomplete, interruption, and quota errors;
- wake-session history retention and reset;
- login/logout cleanup, revocation, and application shutdown;
- continued API-key behavior for the separate OpenAI-compatible backend and
  unchanged TTS routing.

Automated tests alone do not prove that ChatGPT plan usage works. Final
acceptance requires a real end-to-end smoke test:

1. Establish the SSH port forward.
2. Run `chatgpt-login` and complete browser authorization.
3. Select a model returned for the signed-in account.
4. Send a fixed short prompt through the ChatGPT backend.
5. Observe `response.completed` and the expected short response.
6. Restart the bridge and repeat one request to prove credential persistence.

The feature is reported as connected to ChatGPT only after this smoke test
succeeds.

## Documentation changes

The README will distinguish the API-key OpenAI-compatible backend from the SIWC
ChatGPT backend, document the SSH forwarding command, login/logout commands,
model selection, token storage, plan-usage limits, recovery, and the fact that
SIWC does not authenticate TTS.

## Out of scope

- Device Authorization Grant for ChatGPT SIWC.
- Multiple simultaneously saved ChatGPT accounts.
- ChatGPT conversation or memory import.
- Persistent conversation storage across wake sessions.
- Any model tool or agent action.
- SIWC authentication for TTS.
- Paid or remotely hosted multi-user service operation.
