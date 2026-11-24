# OpenAI SDK Device OAuth Design

## Goal

Replace the OpenAI-compatible chat backend's direct `aiohttp` request with the
official asynchronous OpenAI Python SDK, and add OAuth 2.0 Device Authorization
Flow for servers that require browser authorization on another device.

The existing API key flow and OpenAI-compatible Chat Completions behavior must
remain available. This change does not add OAuth to the OpenAI-compatible TTS
adapter.

## Scope

- Use `openai>=2.34,<3` and `AsyncOpenAI` for the OpenAI-compatible chat backend.
- Continue using Chat Completions rather than migrating to the Responses API.
- Support `api_key` and `oauth_device` authentication modes.
- Implement RFC 8628 device authorization, polling, token refresh, persistence,
  and concurrent request coordination.
- Preserve custom base URLs, session headers, conversation history, temperature,
  maximum token settings, extra request fields, and existing TTS routing.
- Document configuration, first login, token storage, refresh, and reauthorization.

## Approaches Considered

### AsyncOpenAI with an asynchronous credential provider

This is the selected approach. `AsyncOpenAI` obtains its bearer credential from
an asynchronous callable backed by a dedicated token manager. The token manager
owns OAuth state while the SDK owns API requests, response parsing, timeouts, and
standard retries. This keeps OAuth independent from chat behavior and makes both
units directly testable.

### AsyncOpenAI with custom httpx authentication

A custom `httpx.AsyncAuth` implementation could inject tokens at the transport
layer. It would couple OAuth state and retry behavior to the SDK transport and
make client lifecycle tests more difficult, without providing a material benefit
for the required device flow.

### Separate OAuth proxy

A local proxy could hide OAuth from the bridge. This provides strong isolation
but adds another deployed service, configuration surface, and failure boundary.
It is unnecessary for the current deployment.

## Architecture

### OpenAIManager

`OpenAIManager` remains the public integration point for chat and conversation
state. It creates one long-lived `AsyncOpenAI` client and calls
`client.chat.completions.create()`.

In `api_key` mode, the client uses the existing key lookup behavior, including
the `OPENAI_API_KEY` environment variable override. In `oauth_device` mode, the
client receives an asynchronous credential provider from
`DeviceOAuthTokenManager`.

Configuration reload closes the old SDK client and OAuth manager before creating
new instances when connection or authentication settings change. Process shutdown
also closes the SDK client and cancels outstanding device authorization polling.

### DeviceOAuthTokenManager

The token manager has one responsibility: return a valid bearer access token.
It performs these operations:

1. Load a previously stored token document.
2. Return the access token while it remains valid beyond the configured refresh
   leeway.
3. Use the refresh token when the access token is approaching expiry.
4. If no refreshable token exists, request a device code and display its
   verification URL, user code, and expiry in the service log.
5. Poll the token endpoint at the server-provided interval until authorization
   succeeds or terminates.
6. Persist each successful token response atomically, including a rotated refresh
   token when supplied.

An `asyncio.Lock` ensures concurrent chat requests share one login or refresh
operation. Secrets and complete token values must never appear in logs.

### Token storage

The default token path is `./data/openai-oauth-token.json`, configurable with
`token_file`. The parent directory is created when necessary. The token file is
written atomically and has mode `0600`.

A malformed token file is renamed with an `.invalid` suffix before a new device
flow begins. Temporary endpoint failures never delete a valid refresh token.

## Configuration

The OpenAI configuration accepts:

```python
"openai": {
    "base_url": "https://example.com/v1",
    "model": "gpt-4o-mini",
    "auth_type": "oauth_device",  # or "api_key"
    "oauth": {
        "device_authorization_endpoint": "https://auth.example.com/oauth/device/code",
        "token_endpoint": "https://auth.example.com/oauth/token",
        "client_id": "open-xiaoai-bridge",
        "scope": "openid offline_access api",
        "audience": "",
        "token_file": "./data/openai-oauth-token.json",
        "refresh_leeway": 120,
    },
}
```

Device flow normally treats the bridge as a public client and does not require a
client secret. Providers that require one may supply it through
`OPENAI_OAUTH_CLIENT_SECRET`; the secret is not included in the example config.

`audience` is omitted from authorization requests when empty. `scope` is sent as
a space-delimited string.

## Request flow

1. The bridge creates a long-lived `AsyncOpenAI` client when the backend is first
   initialized.
2. The first API request asks the credential provider for a bearer token.
3. With no valid stored token, the token manager starts device authorization and
   logs the verification instructions.
4. The user visits the URL on another device and enters the displayed code.
5. The manager polls until authorized, stores the token, and unblocks all waiting
   requests.
6. Later requests reuse the token. A request near expiry refreshes it before the
   SDK sends the API call.
7. If the SDK reports an authentication error, the manager invalidates the access
   token and the application retries the chat request once. A second failure is
   reported without another retry.

## Error handling

- `authorization_pending`: continue at the current interval.
- `slow_down`: increase the polling interval before continuing.
- `access_denied`: stop the login attempt and report the denial.
- `expired_token`: request and display a new device code.
- Invalid refresh token: preserve diagnostic context, clear unusable credentials,
  and begin a new device flow.
- Temporary token endpoint failure: continue using an access token that is still
  valid; otherwise surface the error without deleting the refresh token.
- Corrupt token document: rename it with an `.invalid` suffix and reauthorize.
- SDK network and rate-limit failures: use SDK retry behavior.
- SDK authentication failure: perform no more than one token invalidation and
  application-level retry.

## Compatibility

The SDK request must preserve:

- Custom OpenAI-compatible `base_url` values.
- The configured model and non-streaming Chat Completions call.
- System prompts and bridge-maintained conversation history.
- Optional temperature and maximum token settings.
- `extra_body` fields.
- The optional session header used by compatible agent servers.
- Existing response text extraction behavior for string and structured content.
- Existing API key configuration and environment variable precedence.

## Tests

Unit tests cover:

- Device authorization success, pending authorization, polling slowdown, denial,
  and device code expiry.
- Proactive refresh and rotating refresh token persistence.
- Concurrent access causing only one login or refresh operation.
- Token file permissions, atomic replacement, and corrupt-file recovery.
- API key and device OAuth client construction.
- Preservation of base URL, model, messages, session header, temperature, maximum
  tokens, and extra request fields in the SDK call.
- One authentication retry and no retry loop.
- Client and polling cleanup during configuration reload and shutdown.
- Existing conversation history and response handling behavior.

The README documents configuration, the first-login console interaction, token
storage, refresh behavior, and how to force reauthorization safely.

## Out of scope

- OAuth for OpenAI-compatible TTS.
- Authorization Code callback servers.
- A browser UI embedded in the bridge.
- A separate OAuth proxy service.
- Migration from Chat Completions to the Responses API.
