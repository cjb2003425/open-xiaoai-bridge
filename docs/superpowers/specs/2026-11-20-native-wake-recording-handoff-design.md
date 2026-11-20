# Native-Wake Recording Handoff Design

## Goal

Prevent the first syllables of a user's request from being lost after the L06A native `小爱同学` acknowledgement, without adding another sound or changing ASR models.

## Root Cause

The L06A Rust Client emits `native_kws` only after the firmware-owned acknowledgement has already played, then suppresses the remaining Xiaomi conversation. The Bridge routes this event to the OpenAI conversation controller with `initial_notify=False`.

Despite there being no Bridge notification to play, `ExternalConversationController._conversation_loop()` currently stops and restarts the remote recorder before listening. Restarting `arecord` introduces a capture gap at exactly the time the user begins speaking. Both SenseVoice and Paraformer lost the start of `大西洋有多大`, while adding a leading `请问` made the phrase recognizable, which is consistent with onset clipping rather than a vocabulary-only failure.

## Design

For `initial_notify=False`, the initial conversation setup will:

- not call `_stop_recording()`;
- not call `_play_notify()`;
- not call `_start_recording()`;
- proceed directly to the existing local-ASR turn and `vad.resume("speech")`.

The native wake event can only arrive through the connected Client path that is already forwarding PCM. `vad.resume("speech")` continues to clear stale buffered input and reset VAD state, so acknowledgement audio received before listening is not submitted to ASR.

For `initial_notify=True`, preserve the existing `stop recording → play notification → start recording` sequence. Preserve all stop/start behavior around later TTS responses and following conversation turns.

## Scope

- Modify only the initial setup branch in `ExternalConversationController._conversation_loop()`.
- Update native-wake conversation tests to encode the new handoff contract.
- Do not change VAD thresholds, the 1200 ms silence endpoint, ASR selection, KWS routing, DeepSeek, TTS, Rust Client behavior, or audio buffering internals.
- Do not add a readiness sound, fixed delay, or pre-roll buffer.

## Error Handling

If PCM is unexpectedly unavailable, the existing speech timeout remains the fallback and the conversation exits normally. This change does not introduce a second recorder restart because doing so would recreate the clipping window. Existing service restart and configuration rollback procedures remain unchanged.

## Verification

- A unit test with `initial_notify=False` must assert that initial stop, notify, and start methods are all not awaited.
- A unit test with `initial_notify=True` must assert the existing stop, notify, and start order is preserved.
- Run the native-KWS, wakeup-keyword, audio-stream, and OpenAI test modules.
- Restart the Bridge and verify it loads SenseVoice, reconnects to `10.0.0.5`, and retains VAD silence duration 1200 ms.
- Live-test `大西洋有多大` immediately after the native acknowledgement without a leading filler phrase. The recognized transcript must retain `大西洋`.

## Rollback

Revert the conversation-loop branch and its tests, restart the user service, and verify the previous startup logs. No configuration or speaker-side rollback is required.
