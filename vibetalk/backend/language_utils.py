"""
language_utils.py — shared language/voice metadata for Bridge Mode.

Put this in: vibetalk/backend/language_utils.py

This has no network calls — it's just lookup tables and small helper
functions used by gnani_client.py, translator.py, and the bridge pipeline.
"""

# STT (Prisma) supported language codes -> human-readable name.
# Used for validating input and for building clear translation prompts.
STT_LANGUAGES = {
    "bn-IN": "Bengali",
    "en-IN": "English",
    "gu-IN": "Gujarati",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

# TTS (Timbre v2.5) supported language codes. Includes everything STT has,
# plus "hi-en" (Hinglish) and "auto" (auto-detect from script), which STT
# does not have as direct options.
TTS_LANGUAGES = set(STT_LANGUAGES.keys()) | {"hi-en", "auto"}

# Voice to use per target TTS language.
# IMPORTANT: this only lists voices confirmed in Gnani's docs
# (Nalini, Kaveri, Deepak, Poorvi as named examples for Timbre v2.5).
# Before relying on this for languages beyond Hindi/English/Hinglish,
# check the real "Timbre v2.5 voices" catalog page and replace the
# "Nalini" fallbacks below with a voice that actually supports/prefers
# that language — using the wrong voice for a language may sound
# wrong or the API may reject it.
VOICE_FOR_LANGUAGE = {
    "hi-IN": "Nalini",
    "en-IN": "Kaveri",
    "hi-en": "Poorvi",
    # Fallback for every other language until confirmed from the voice
    # catalog. Replace these as you verify each one.
    "te-IN": "Nalini",
    "ta-IN": "Nalini",
    "kn-IN": "Nalini",
    "ml-IN": "Nalini",
    "mr-IN": "Nalini",
    "pa-IN": "Nalini",
    "bn-IN": "Nalini",
    "gu-IN": "Nalini",
    "auto": "Nalini",
}


def is_supported_stt_language(code: str) -> bool:
    # "indic-auto" isn't a language — it tells Prisma v3.0 to detect the
    # spoken language itself, so it's valid as an INPUT to transcription
    # even though it never appears as a detected/output language.
    return code in STT_LANGUAGES or code == "indic-auto"


def is_supported_tts_language(code: str) -> bool:
    return code in TTS_LANGUAGES


def language_name(code: str) -> str:
    """Human-readable name for a language code, for prompts/logging."""
    if code in STT_LANGUAGES:
        return STT_LANGUAGES[code]
    if code == "hi-en":
        return "Hinglish"
    if code == "auto":
        return "auto-detected language"
    return code


def voice_for(language_code: str) -> str:
    """Pick a TTS voice for a given target language, with a safe fallback."""
    return VOICE_FOR_LANGUAGE.get(language_code, "Nalini")