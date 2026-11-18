# “Hey 小智” Wake Word Design

## Goal

Replace all custom wake phrases with the single phrase “Hey 小智” while keeping the custom speaker path routed to the OpenAI-compatible backend configured for direct DeepSeek access.

## Runtime Behavior

- `wakeup.keywords` contains exactly `hey 小智`.
- “你好小黑”, “小黑你好”, “你好小智”, “小智小智”, and all other prior custom wake phrases no longer trigger local KWS.
- A local KWS result for “Hey 小智” plays “我在” and returns the `openai` route.
- Ending that conversation plays “小智，再见”.
- The `xiaozhi` backend remains disabled; the word “小智” in this phrase does not select the XiaoZhi WebSocket backend.
- The OpenAI-compatible backend remains configured for `https://api.deepseek.com/v1` with model `deepseek-chat` and no Hermes session header.
- Native “小爱同学” remains blocked by the device-side PCM tap hook and is outside this configuration change.

## Keyword Generation

Restarting Bridge regenerates `core/models/keywords.txt` from the active external config. The generated file must contain exactly one non-empty keyword entry whose result label represents `HEY小智`.

The phrase is stored with lowercase English in config, following the repository's wake-word rule. The generator uppercases it before Sherpa tokenization.

## Failure Safety

- If keyword generation fails, Bridge must not be reported as validated.
- A missing DeepSeek API key does not prevent KWS validation: “Hey 小智” must still play “我在”, after which the backend request may fail until a key is configured.
- No Hermes endpoint or credential is introduced by this change.

## Validation

1. Run the existing wake-keyword tests and config syntax checks.
2. Restart Bridge and verify KWS/VAD startup logs.
3. Verify `keywords.txt` has one entry for `HEY小智`.
4. Say “你好小黑” and verify no trigger.
5. Say “Hey 小智” and verify the speaker plays “我在” and Bridge logs one KWS wake event routed to `openai`.
6. Say “小爱同学” and verify the native assistant remains silent.
