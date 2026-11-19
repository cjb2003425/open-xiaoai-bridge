# Native XiaoAI KWS to DeepSeek Design

## Goal

Use the L06A speaker's native `小爱同学` keyword spotter only as the wake
detector.  The Xiaomi conversation must be stopped immediately.  The speaker
then acknowledges locally with `我在`, captures the user's next utterance via
the existing PCM tap, transcribes it with the Bridge's local ASR, and sends it
directly to DeepSeek through the OpenAI-compatible backend.

Hermes, `xiaozhi-esp32-server`, the Bridge-side Sherpa keyword spotter, and
Xiaomi's response path are outside this flow.

## User Interaction

The interaction is deliberately two-stage:

1. User says `小爱同学`.
2. The speaker locally says `我在` exactly once.
3. User asks a question.
4. The Bridge performs local VAD and ASR, calls DeepSeek, and plays the answer.

The local acknowledgement is the ready cue.  The Bridge must not play its
normal initial notification sound for this wake source.

## Existing Signals and Constraints

The L06A instruction log already shows a stable native-wake marker:

- `header.namespace == "SpeechRecognizer"`
- `header.name == "RecognizeResult"`
- empty recognized text
- `payload.is_final == false`
- `payload.is_vad_begin == false`
- a non-empty `header.dialog_id`

Later `RecognizeResult` entries contain the user's speech.  The first empty
marker, not those later entries, is the takeover boundary.

The current preload hook publishes valid 16 kHz mono S16LE PCM before it
silences the native DNN output.  This design keeps the hook and ring but turns
off `OPEN_XIAOAI_BLOCK_NATIVE_WAKE`, allowing Xiaomi's native KWS to produce
the marker.  The existing PCM tap remains the audio source for local VAD and
ASR after takeover.

The DeepSeek API key is runtime-only secret material.  It must be stored in the
external Bridge environment file with mode `0600`; it must never appear in a
repository file, generated config, test fixture, command output, or log.

## Architecture

### Speaker Client

The Rust Client parses new lines from
`/tmp/mico_aivs_lab/instruction.log` as `LogMessage`.  A dedicated native-wake
guard recognizes the marker above and deduplicates by `dialog_id`.

For a new marker it performs these actions in order:

1. Stop Xiaomi playback and native listening locally.
2. Play `我在` locally and wait for it to finish.
3. Send a structured `native_kws` event containing the `dialog_id` and the
   result of the local suppression attempt.

The guard remains active independently of WebSocket connectivity.  If the
Bridge is unavailable, it still suppresses Xiaomi but does not start an AI
conversation.  Reconnection must not replay an old `dialog_id`.

The Client is supervised on the speaker and starts before native wake blocking
is disabled.  This minimizes the interval in which native KWS could run
without the takeover guard.

### Bridge Event Handling

`XiaoAI.on_event` accepts the structured `native_kws` event and validates its
payload.  Duplicate `dialog_id` values are ignored.  If the Client reports
that local suppression failed, the Bridge retries the existing remote
suppression operation before proceeding.

The event enters `EventManager.wakeup` through a dedicated `native_kws` source.
The wake hook returns the `openai` route without playing another
acknowledgement.  The OpenAI conversation controller skips its initial notify
sound for this source, starts recording, and immediately waits for the user's
post-acknowledgement question.

### Audio Services

`AUDIO_INPUT_ENABLE` remains enabled because the PCM stream, VAD, and local
ASR are required after wakeup.  A separate KWS enable switch disables only the
Bridge-side Sherpa keyword spotter.  The application must therefore be able to
start VAD without starting KWS.

The temporary KWS diagnostic counters and PCM dump used during investigation
are removed.  No continuous diagnostic audio recording remains in production.

### Backend

The OpenAI-compatible manager continues to use:

- base URL: `https://api.deepseek.com/v1`
- model: `deepseek-chat`
- input mode: `local_asr`
- no Hermes session header

The runtime environment supplies the DeepSeek API key.  Startup logs may state
whether a key is configured but must never print it.

## Failure Handling

- Client disconnected: suppress native Xiaomi locally; do not acknowledge or
  begin a conversation that cannot reach the Bridge.
- Duplicate marker: ignore it by `dialog_id`.
- Local suppression failure: report the failure and let the Bridge retry once.
- Local acknowledgement failure: report it; the Bridge may still take over,
  but logs the degraded interaction.
- Invalid event payload: reject it without starting a conversation.
- DeepSeek authentication or request failure: play the existing backend error
  response; never fall back to Hermes, xiaozhi, or Xiaomi.
- Client crash: the speaker supervisor restarts it.  Deployment does not
  disable native blocking until supervision and event handling are healthy.

## Deployment Sequence

1. Remove the abandoned Bridge KWS diagnostic instrumentation.
2. Add and test the Client native-wake guard while native wake blocking remains
   enabled.
3. Add and test Bridge `native_kws` routing and the independent KWS enable
   switch.
4. Install the DeepSeek key in the protected runtime environment and perform a
   minimal authenticated request without logging the secret.
5. Deploy and supervise the new Client.
6. Temporarily disable `OPEN_XIAOAI_BLOCK_NATIVE_WAKE` and restart only the
   native audio process required to apply it.
7. Run physical acceptance tests.
8. Persist the configuration only after all acceptance checks pass.

At any failure before step 8, restore
`OPEN_XIAOAI_BLOCK_NATIVE_WAKE=1`, restart the native audio process, and verify
that `小爱同学` is silent again.

## Tests and Acceptance

Automated tests cover:

- Rust parsing of the exact native wake marker;
- rejection of non-wake `RecognizeResult` entries;
- per-`dialog_id` deduplication;
- local suppression before acknowledgement and event emission;
- continued local suppression when the WebSocket is unavailable;
- Python validation and deduplication of `native_kws` events;
- routing `native_kws` to `openai` without a second acknowledgement;
- starting VAD with Bridge-side KWS disabled;
- preserving existing OpenAI/DeepSeek header and secret handling tests.

Physical acceptance requires:

1. Five consecutive `小爱同学` attempts each produce exactly one `我在` and
   no Xiaomi answer.
2. A question spoken after `我在` is transcribed locally and sent directly to
   DeepSeek.
3. Disconnecting the Bridge still prevents a Xiaomi answer.
4. Restarting the Bridge and Client preserves behavior.
5. Reboot persistence is tested only after the temporary deployment passes.
6. Rollback restores the current fully silent native-wake behavior.

## Out of Scope

- Custom wake phrases such as `Hey 小智` or `嘿小智`.
- Xiaomi cloud ASR for the user's question.
- Hermes and xiaozhi backends.
- A new on-device Sherpa model.
- Changes to DeepSeek prompts, voices, or conversation policy beyond routing.
