# ASR Model Benchmark Documentation Design

## Goal

Create a durable repository document at
`docs/asr-model-benchmark-2026-11-21.md` that records the local ASR comparison
performed on 2026-11-21. The document must let a future maintainer understand
what was tested, compare every candidate fairly, and avoid repeating rejected
experiments without evidence.

## Scope

The report will cover the two benchmark rounds that used the same 16
SiliconFlow CosyVoice2 samples: eight utterances spoken with the `alex` voice
and the same eight spoken with the `anna` voice. It will include:

- the Intel N100 CPU environment, sherpa-onnx 2.0.2, ONNX Runtime 1.23.2,
  CPU provider, and two inference threads;
- the exact eight reference utterances, including Chinese proper nouns,
  foreign names, numbers, and Chinese-English code switching;
- normalization and scoring rules for exact-match accuracy and character
  error rate (CER);
- model load time, average and maximum decode time, peak RSS, exact matches,
  and CER for every tested model or mode;
- representative raw transcripts for `大西洋有多大`, foreign names, and
  Chinese-English mixed utterances;
- the FunASR-Nano baseline versus dynamic-hotword experiment;
- limitations caused by synthetic TTS pronunciation and equivalent numeric
  rendering; and
- a decision summary that distinguishes best accuracy, best latency, useful
  compromise, and rejected candidates.

## Models and Modes

The comparison table will include:

1. Qwen3-ASR 0.6B INT8
2. FunASR-Nano INT8
3. FunASR-Nano INT8 with dynamic hotwords
4. Paraformer Large INT8
5. Paraformer trilingual INT8
6. SenseVoice 2025 INT8
7. SenseVoice 2024 INT8
8. FireRedASR2 CTC INT8
9. Nemotron 3.5 ASR Streaming 0.6B INT8, 560 ms chunks
10. Omnilingual ASR 300M CTC INT8

## Structure

The report will use the following sections:

1. Purpose and executive summary
2. Test environment
3. Audio corpus and scoring method
4. Complete comparison table
5. Per-utterance findings and representative transcripts
6. Dynamic-hotword findings
7. Benchmark limitations
8. Recommendation and active-service status

The complete comparison table is the primary reference. Detailed transcript
sections will remain selective so the report is readable while still exposing
the errors that drove the recommendation.

## Data Integrity and Privacy

- Use the measured values recorded during the benchmark; do not estimate or
  silently round values beyond the displayed precision.
- State that all models received the same 16 kHz mono PCM samples.
- State that strict exact matching ignores punctuation, whitespace, and case.
- Explain that CER and exact-match scores still penalize equivalent renderings
  such as `85%` versus `百分之八十五`.
- Do not include API keys, access tokens, speaker credentials, or secret
  configuration values.
- Do not imply that synthetic-TTS accuracy is equivalent to live far-field
  microphone accuracy.

## Repository Changes

Implementation will create only
`docs/asr-model-benchmark-2026-11-21.md`. README changes and runtime changes are
out of scope. The report will not activate an ASR model, restart the service,
or modify the existing dirty `config.py`.

## Validation

Before committing the report:

- verify every table value against the recorded benchmark summary;
- check that model names and modes are unambiguous;
- scan for placeholders, secrets, contradictions, and unsupported claims;
- run `git diff --check`;
- confirm the only staged implementation file is the benchmark report; and
- confirm the active Bridge service remains unchanged.

## Acceptance Criteria

The task is complete when the dedicated report exists, contains all ten model
or mode rows with accurate measurements, documents the representative errors
and limitations, and clearly records these recommendations:

- Paraformer Large for lowest-latency daily interaction;
- Qwen3-ASR 0.6B for highest strict accuracy;
- FunASR-Nano as a possible compromise, with a warning that long hotword lists
  increase latency and can interfere with unrelated output;
- Nemotron 3.5, Omnilingual, and FireRedASR2 rejected for this N100 workload;
  and
- SenseVoice 2024 remains the active service model unless separately changed.
