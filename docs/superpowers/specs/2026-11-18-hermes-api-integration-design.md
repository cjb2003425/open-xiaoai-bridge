# Hermes API Integration Design

## Goal

Connect Open-XiaoAI Bridge to the existing local Hermes Agent Gateway through Hermes's OpenAI-compatible API. Voice input uses the Bridge's local VAD, keyword spotting, and ASR pipeline. Replies play through XiaoAI's native TTS.

## Architecture

- Enable the existing Hermes Gateway API Server on `127.0.0.1:8642`.
- Keep the Hermes API loopback-only and protect it with a generated API key.
- Enable the Bridge OpenAI-compatible backend and point it to `http://127.0.0.1:8642/v1`.
- Use Hermes's virtual `hermes-agent` model so Hermes keeps ownership of its configured provider, model, tools, memory, and authentication.
- Send `X-Hermes-Session-Key` with the Bridge session key to preserve the Hermes long-term memory scope.

## Voice Interaction

- Enable local audio input, VAD, KWS, and ASR.
- Download the project-pinned model bundle when the required model files are absent.
- Route “你好小黑” and “小黑你好” to the Hermes-backed OpenAI-compatible conversation controller.
- Retain “召唤小黑” as the XiaoAI-native command that enters the same conversation mode.
- Use XiaoAI native TTS for Hermes responses; no external TTS credentials are required.
- Retain the existing exit words: “退出”, “停止”, and “再见”.

## Configuration and Secrets

- Store the Hermes API key in user-owned configuration with mode `0600`.
- Do not commit the key to either repository.
- Keep the Bridge runtime configuration outside the tracked source checkout.
- Keep the Hermes API listener on loopback; only the Bridge ports remain available to the LAN.

## Runtime Behavior

- The existing Hermes Gateway service remains the single Hermes process owner.
- The Bridge calls Hermes through standard non-streaming `POST /v1/chat/completions` requests.
- The Bridge keeps its bounded recent message history and Hermes receives the stable session key for its own memory scope.
- If Hermes is temporarily unavailable, the Bridge continues serving its device and health endpoints and logs the backend failure.

## Verification

1. Confirm the Hermes Gateway API Server listens only on `127.0.0.1:8642`.
2. Confirm authenticated Hermes health, capabilities, and Chat Completions requests succeed.
3. Confirm all required VAD, KWS, and ASR model files exist and keyword generation succeeds.
4. Confirm the Bridge service restarts successfully with the OpenAI-compatible backend enabled.
5. Confirm the Bridge health endpoint remains healthy and logs show the Hermes backend enabled.
6. Confirm the tracked Open-XiaoAI Bridge checkout remains clean except for this design document commit.

## Rollback

Disable the Bridge OpenAI-compatible backend, restore its prior runtime configuration, and disable the Hermes API Server platform. The base XiaoAI Bridge and HTTP health service remain available.
