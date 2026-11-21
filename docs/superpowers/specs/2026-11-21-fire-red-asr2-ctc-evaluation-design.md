# FireRedASR2 CTC Evaluation Design

## Goal

Evaluate `sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25` as a
lower-latency local ASR candidate for the XiaoAI bridge. Keep the active
SenseVoice 2024 service unchanged unless the candidate passes the offline gate
and a temporary live-speaker trial.

## Scope and invariants

- Use the existing native XiaoAI wake-word path and speaker at `10.0.0.5`.
- Keep server-side KWS disabled.
- Keep the current VAD, direct DeepSeek, TTS, and boot-service behavior.
- Do not use Docker or Hermes.
- Do not change the active ASR configuration during download or offline tests.
- Preserve the user's existing uncommitted files.

## Candidate and runtime

Use the official sherpa-onnx INT8 package
`sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25`. The installed
`sherpa-onnx==2.0.2` runtime provides
`OfflineRecognizer.from_fire_red_asr_ctc(model, tokens, num_threads=1,
decoding_method="greedy_search", debug=False, provider="cpu")`.

Download the official archive into a temporary directory. Before extraction,
record its byte size and SHA-256 digest and require the bzip2 integrity check to
pass. Inspect archive members and require a single expected top-level directory.
After extraction, require non-empty `model.int8.onnx` and `tokens.txt` files,
then install the model under `core/models/`.

## Offline evaluation

Benchmark the candidate before changing tracked adapter code. Use the same WAV
used for the Qwen3-ASR evaluation, identified by SHA-256
`a552ff98cc47ab058ddf4946d7a0643efee30fc79628b8872e4a79336bccee26`.
Select the speech-containing region beginning at 11 seconds, whose expected
content is `嘿小智`.

Decode identical 3-, 5-, and 10-second slices three times each with
`num_threads=2`, greedy search, and the CPU provider. Record:

- recognizer load time;
- every decode time, plus average and maximum by duration;
- every transcript;
- resident memory and swap usage.

The candidate passes only if all nine transcripts contain the semantic content
`嘿小智` after ignoring punctuation and spacing, and every decode completes in
no more than 3.0 seconds. A timeout, empty result, incorrect content, process
failure, swap pressure, or decode above 3.0 seconds fails the gate. A failed
gate ends the evaluation without adapter or live-service changes.

## Adapter integration after a passing gate

Add a distinct `fire_red_asr_ctc` backend to the existing sherpa adapter. It
selects `model.int8.onnx` when INT8 is enabled (and `model.onnx` otherwise),
requires `tokens.txt`, and calls `from_fire_red_asr_ctc` with only:

- `model`;
- `tokens`;
- `num_threads=2`;
- `decoding_method="greedy_search"`;
- `debug=False`;
- `provider="cpu"`.

The CTC factory does not accept the adapter's homophone-replacer arguments.
Model capability metadata must therefore control whether those arguments are
passed, while preserving current behavior for SenseVoice, Paraformer, and the
existing FireRedASR AED backend. Qwen3-ASR remains token-free and unchanged.

Use test-driven development for model layout selection and exact factory
arguments. Run focused adapter tests and the complete repository test suite
before any live configuration change.

## Temporary live-speaker trial

Only after the offline gate and tests pass, back up and hash the active external
configuration. Change only the ASR backend, model directory, and INT8 selection
needed for FireRedASR2 CTC. Restart the bridge and require:

- the service to remain active without restart loops or tracebacks;
- a startup log naming `fire_red_asr_ctc` and the exact candidate directory;
- the speaker at `10.0.0.5` to reconnect;
- native XiaoAI KWS, current VAD, DeepSeek, and TTS settings to remain intact.

The user will test `嘿小智`, `大西洋`, two or three difficult foreign names, a
Chinese-English mixed sentence, and a longer sentence containing a short pause.
Accept the candidate only if it produces no empty results or stability failures,
feels acceptably responsive, and is more accurate overall than SenseVoice for
those utterances.

## Failure handling and rollback

Download, validation, smoke-test, benchmark, or integration failures leave the
active SenseVoice service untouched. A live-trial failure restores only the
backed-up ASR fields, restarts the bridge, and verifies SenseVoice 2024, the
existing VAD, disabled server KWS, direct DeepSeek path, and speaker connection.

Retain a valid candidate model after a failed accuracy or latency gate for later
offline optimization. Remove only corrupt or incomplete temporary artifacts,
using a recoverable operation where practical.

## Verification and deliverables

The evaluation records archive identity and integrity, model file validation,
smoke-test output, the nine-run benchmark, memory use, test-suite results, and
the final active model. Tracked deliverables are the design and implementation
plan plus adapter/tests only when the offline gate passes. The active service
must end in a healthy state on either the accepted candidate or the original
SenseVoice 2024 model.
