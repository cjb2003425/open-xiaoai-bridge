# DeepSeek ASR Proper-Name Correction Design

## Goal

Improve answers to spoken questions containing arbitrary foreign names without adding a second model request or slowing the existing local SenseVoice ASR path.

## Scope

- Add guidance to the existing DeepSeek `system_prompt`.
- Cover likely speech-recognition errors in foreign personal names, place names, and organization names.
- Keep SenseVoice, VAD, native XiaoAI KWS, DeepSeek model selection, conversation history, TTS, and response formatting unchanged.
- Do not install a general synonym dictionary or claim that the raw ASR transcript has been corrected.

## Behavior

The system prompt will state that user messages may be produced by speech recognition. When a term resembles an incorrectly transcribed foreign proper noun, DeepSeek should use pronunciation, nearby words, conversation history, and the question's subject to infer the most likely standard Chinese translation or original name.

When the intended name is clear, DeepSeek should answer the intended question directly without announcing the correction. When multiple interpretations remain plausible and would materially change the answer, it should ask one concise clarification question instead of inventing a name.

The existing plain-text, no-Markdown, and 300-Chinese-character response constraints remain in the separate `rule_prompt` and are not duplicated.

## Data Flow

1. Local SenseVoice produces the transcript as it does today.
2. The bridge sends that transcript, conversation history, and the new system prompt in the existing DeepSeek request.
3. DeepSeek resolves likely proper-name transcription errors while producing its normal answer.
4. The existing TTS path plays the answer.

No extra API request or local inference pass is added.

## Configuration and Reload

The repository default configuration and the active configuration at `/home/torrey/.config/open-xiaoai-bridge/config.py` will use the same non-empty `openai.system_prompt`. The existing configuration watcher should hot-reload it. A service restart is allowed only if the reload is not observed.

## Error Handling

- Preserve the transcript when no proper-name ambiguity exists.
- Ask for clarification when contextual inference is uncertain.
- Do not silently substitute unrelated common words or alter numerical facts.
- A missing or failed DeepSeek request follows the bridge's existing error path; no new fallback service is introduced.

## Verification

- Verify the active and repository configurations contain identical prompt text.
- Verify the service logs a successful runtime configuration reload and remains active.
- Speak several questions containing foreign names, including one recognizable name and one ambiguous phonetic form.
- Confirm clear cases receive a relevant direct answer and ambiguous cases receive a short clarification question.
- Confirm no second DeepSeek request is generated for a single spoken turn.
