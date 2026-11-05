"""
test_bridge_pipeline.py — end-to-end Bridge Mode test.

Put this in: vibetalk/backend/test_bridge_pipeline.py
(alongside gnani_client.py, translator.py, language_utils.py — this script
imports all three, so they must be in the same folder)

Full chain tested here, one walkie-talkie turn:
    audio in (source language) -> Prisma transcribes
    -> translator converts text to target language
    -> Timbre speaks the translated text -> saved as bridge_output.wav

Usage:
    python test_bridge_pipeline.py speaker_audio.wav te-IN hi-IN

    speaker_audio.wav  — what Person A said
    te-IN              — language Person A is speaking (source)
    hi-IN              — language Person B understands (target)

Edge cases explicitly handled, each with a clear message instead of a
crash or silent wrong output:
    1. Missing/invalid audio file
    2. Unsupported audio format (delegated to gnani_client, auto-converts)
    3. Unsupported source or target language code
    4. Silence / no speech detected in the audio (empty transcript)
    5. Same source and target language (skips translation entirely —
       useful as a "practice mode" / sanity check of STT+TTS alone)
    6. Translation service not configured (.env missing LLM_* vars)
    7. Translation API failure
    8. TTS failure after a successful translation (translated text is
       still printed to the terminal so it isn't lost)
    9. Any Gnani API error at any stage (auth, credits, rate limit,
       server error) — reported with the real error type/message, not
       a generic failure
"""

import os
import sys

from language_utils import (
    is_supported_stt_language,
    is_supported_tts_language,
    language_name,
    voice_for,
)
from gnani_client import transcribe, synthesize, GnaniAPIError
from translator import translate_text, TranslationError

OUTPUT_FILE = "bridge_output.wav"


def run_bridge(audio_path: str, source_lang: str, target_lang: str) -> None:
    # --- Edge case: file existence is checked inside gnani_client.transcribe,
    # but we check here too so we can fail fast with a clear message before
    # doing any language validation work.
    if not os.path.exists(audio_path):
        print(f"ERROR: audio file not found: {audio_path}")
        sys.exit(1)

    # --- Edge case: unsupported language codes, checked before any network call.
    if not is_supported_stt_language(source_lang):
        print(
            f"ERROR: '{source_lang}' is not a supported STT language. "
            f"Supported: bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, "
            f"pa-IN, ta-IN, te-IN"
        )
        sys.exit(1)

    if not is_supported_tts_language(target_lang):
        print(
            f"ERROR: '{target_lang}' is not a supported TTS language. "
            f"Supported: the 10 STT languages above, plus hi-en and auto."
        )
        sys.exit(1)

    print(f"Step 1/3 — Transcribing '{audio_path}' as {language_name(source_lang)}...")
    try:
        stt_result = transcribe(audio_path, language_code=source_lang)
    except GnaniAPIError as e:
        print(f"ERROR during transcription: {e}")
        sys.exit(1)

    transcript = stt_result.transcript
    if stt_result.detected_language_code:
        print(f"  (Prisma v3.0 detected: {stt_result.detected_language_code})")

    # --- Edge case: silence or unintelligible audio. Not a crash — just
    # nothing to translate or speak, and the user should know why.
    if not transcript:
        print(
            "No speech detected in the audio (empty transcript). "
            "Nothing to translate or speak back. Try a clearer or louder "
            "recording."
        )
        return

    print(f"  Transcript ({language_name(source_lang)}): {transcript}")

    # --- Edge case: same source and target language — skip translation
    # entirely rather than sending identical-language text to the LLM.
    if source_lang == target_lang:
        print(
            "Step 2/3 — Source and target language are the same, "
            "skipping translation."
        )
        translated_text = transcript
    else:
        print(
            f"Step 2/3 — Translating {language_name(source_lang)} -> "
            f"{language_name(target_lang)}..."
        )
        try:
            translated_text = translate_text(transcript, source_lang, target_lang)
        except TranslationError as e:
            print(f"ERROR during translation: {e}")
            print(f"(Original transcript was: {transcript})")
            sys.exit(1)

        if not translated_text:
            print("ERROR: translation returned empty text unexpectedly.")
            sys.exit(1)

        print(f"  Translated ({language_name(target_lang)}): {translated_text}")

    print(f"Step 3/3 — Synthesizing speech in {language_name(target_lang)}...")
    voice = voice_for(target_lang)
    try:
        audio_bytes = synthesize(translated_text, voice=voice, language=target_lang)
    except GnaniAPIError as e:
        # --- Edge case: don't lose the translated text just because TTS
        # failed — print it so the "conversation" can still continue
        # manually if needed.
        print(f"ERROR during speech synthesis: {e}")
        print(f"(Translated text was preserved: {translated_text})")
        sys.exit(1)

    with open(OUTPUT_FILE, "wb") as f:
        f.write(audio_bytes)

    print(f"\nDone. Saved translated audio to {OUTPUT_FILE}")
    print(f"Play it back to hear: \"{translated_text}\"")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "Usage: python test_bridge_pipeline.py "
            "<audio_file> <source_language_code> <target_language_code>"
        )
        print("e.g.:  python test_bridge_pipeline.py telugu.mp4 te-IN hi-IN")
        sys.exit(1)

    run_bridge(sys.argv[1], sys.argv[2], sys.argv[3])