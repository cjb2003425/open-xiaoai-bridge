# Paraformer-Large ASR Upgrade Design

## Goal

Evaluate and, only if it passes explicit quality and performance gates, switch the local ASR backend from SenseVoiceSmall INT8 to the official Sherpa-ONNX Paraformer Chinese-English INT8 model while keeping local ASR computation below one second for a ten-second utterance on the Intel N100 host.

## Model

- Candidate: `sherpa-onnx-paraformer-zh-2024-03-09`, INT8.
- Source: the official Sherpa-ONNX offline Paraformer model catalog and release assets.
- Languages: Chinese and English.
- Runtime: the installed Sherpa-ONNX Python runtime and its existing `OfflineRecognizer.from_paraformer` integration.
- Install the candidate beside existing models under `core/models/`; do not delete SenseVoice or the existing trilingual Paraformer model.

References:

- <https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-paraformer/index.html>
- <https://github.com/modelscope/FunASR/blob/main/docs/model_selection.md>

## Scope

- Download, extract, validate, and benchmark the candidate model.
- Compare the candidate against the currently active SenseVoiceSmall INT8 model using identical audio.
- Switch the active backend only after the candidate passes the gates below.
- Keep VAD, native XiaoAI KWS, DeepSeek, TTS, and speaker-client behavior unchanged.
- Do not add FunASR/PyTorch, SeACo hotword injection, or Fun-ASR-Nano in this change.

## Evaluation

### Performance gate

Use warm recognizers and identical 16 kHz mono PCM input. Record at least three runs each for 3-second, 5-second, and 10-second audio. The candidate passes when every measured 10-second decode is below 1.0 second. Model initialization time is reported separately and is not counted as per-utterance decode latency because the bridge preloads ASR at service startup.

### Quality gate

Compare both models on the same representative spoken phrases. The set must include ordinary Mandarin questions, Chinese sentences containing English words, and several foreign personal names. Preserve both transcripts for side-by-side user review. The candidate passes only when ordinary Mandarin does not materially regress and foreign-name results are better overall or no worse than SenseVoice.

The evaluation does not claim that Paraformer can recognize every arbitrary foreign name. This Sherpa-ONNX Paraformer interface does not provide dynamic hotword injection.

### Resource gate

Record model directory size, process resident memory after warmup, and service startup success. Reject the candidate if it causes persistent memory pressure, crashes, or repeated model-loading errors.

## Activation

If all gates pass:

- Set `APP_CONFIG["asr"]["model"]` to `"paraformer"` in the repository and active configuration.
- Set `APP_CONFIG["asr"]["model_dir"]` to the exact candidate directory name so directory scanning cannot select the existing trilingual model.
- Keep `APP_CONFIG["asr"]["int8"]` enabled.
- Restart the user-level `open-xiaoai-bridge.service`, because the ASR singleton does not replace an already-loaded recognizer during config hot reload.
- Confirm logs name the selected Paraformer directory and the service reconnects to the speaker.

## Rollback

If any gate fails, leave SenseVoice active and retain the candidate only for offline testing. If a post-activation problem appears, restore `model="sense_voice"`, restore the pinned SenseVoice `model_dir` or remove the Paraformer override as appropriate, restart the service, and verify the SenseVoice startup log.

## Acceptance

- The active service remains healthy and reconnects to the speaker.
- Ten-second candidate inference stays below one second on the N100 in repeated warm runs.
- User review accepts the side-by-side transcript quality, including foreign-name examples.
- No existing model is removed, and rollback requires only configuration changes plus one service restart.
