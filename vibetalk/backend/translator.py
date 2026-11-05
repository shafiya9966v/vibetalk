"""
translator.py — translates text between languages using deep-translator
(MyMemory backend), chosen for Bridge Mode because it needs no GPU,
no API key, and runs reliably on a CPU-only laptop.

Why MyMemory instead of GoogleTranslator?
deep-translator's GoogleTranslator hits Google's unofficial/scrape endpoint
which gets rate-limited after just a few requests per session. MyMemory is
a proper, documented free translation API (mymemory.translated.net) that
allows ~1000 words/day without a key and is designed for programmatic use.

Put this in: vibetalk/backend/translator.py (replaces the LLM-based version)

Same public interface as before — translate_text(text, source_lang_code,
target_lang_code) — so gnani_client.py, test_bridge_pipeline.py, and
anything else that imports this module does NOT need to change.

An LLM-based alternative (better for natural, code-mixed/casual phrasing,
but needs an API key and network-dependent provider) is kept at
translator_llm_fallback.py — rename it to translator.py later if you want
to switch, since both expose the same translate_text() signature.

SETUP REQUIRED:
    pip install deep-translator

Edge cases handled:
- Empty / whitespace-only input -> returns "" without calling anything
- source_lang_code == target_lang_code -> returns the input unchanged,
  no translation call made
- Unsupported/unmapped language code -> raises a clear TranslationError
  before attempting the call, instead of a confusing downstream error
- deep-translator / network failure -> one retry, then raises
  TranslationError with the real underlying message
- Text longer than Google Translate's ~5000 character single-request
  limit -> raises a clear TranslationError rather than a silent truncation
  (shouldn't happen for spoken sentences, but guarded anyway)
- Library not installed -> clear instruction to pip install it, instead
  of a raw ImportError traceback
"""

import time

from language_utils import language_name

try:
    from deep_translator import MyMemoryTranslator
    from deep_translator.exceptions import (
        LanguageNotSupportedException,
        NotValidPayload,
        TranslationNotFound,
    )
    _IMPORT_ERROR = None
except ImportError as e:
    MyMemoryTranslator = None
    LanguageNotSupportedException = NotValidPayload = TranslationNotFound = Exception
    _IMPORT_ERROR = e


class TranslationError(Exception):
    pass


# MyMemory uses "xx-YY" locale format — which matches our internal codes
# almost exactly. The only difference is "hi-en" (Hinglish) which MyMemory
# doesn't have as a source; we map it to "hi-IN" for translation purposes.
_LANG_CODE_MAP = {
    "bn-IN": "bn-IN",
    "en-IN": "en-IN",
    "gu-IN": "gu-IN",
    "hi-IN": "hi-IN",
    "hi-en": "hi-IN",   # Hinglish -> treat as Hindi for translation
    "kn-IN": "kn-IN",
    "ml-IN": "ml-IN",
    "mr-IN": "mr-IN",
    "pa-IN": "pa-IN",
    "ta-IN": "ta-IN",
    "te-IN": "te-IN",
}

_MAX_CHARS = 4500  # safety margin under Google Translate's ~5000 char limit


def _to_translator_code(code: str) -> str:
    if code not in _LANG_CODE_MAP:
        raise TranslationError(
            f"'{code}' has no known deep-translator language mapping. "
            f"Supported: {', '.join(sorted(_LANG_CODE_MAP.keys()))}"
        )
    return _LANG_CODE_MAP[code]


def translate_text(text: str, source_lang_code: str, target_lang_code: str) -> str:
    """
    Translate `text` from source_lang_code to target_lang_code (our
    internal "xx-IN" style codes — same ones used for STT/TTS).

    Returns the translated text. If source_lang_code == target_lang_code,
    returns the input unchanged without calling the translator at all.
    """
    if not text or not text.strip():
        return ""

    text = text.strip()

    if source_lang_code == target_lang_code:
        return text

    if MyMemoryTranslator is None:
        raise TranslationError(
            f"deep-translator is not installed ({_IMPORT_ERROR}). "
            "Run: pip install deep-translator"
        )

    if len(text) > _MAX_CHARS:
        raise TranslationError(
            f"Text is {len(text)} characters, over the {_MAX_CHARS}-char "
            "safety limit for a single translation request. This "
            "shouldn't happen for a single spoken turn — check upstream "
            "logic if it does."
        )

    source_code = _to_translator_code(source_lang_code)
    target_code = _to_translator_code(target_lang_code)

    last_error = None
    for attempt in range(3):  # 3 attempts: MyMemory is reliable but can blip
        try:
            translator = MyMemoryTranslator(source=source_code, target=target_code)
            result = translator.translate(text)
            if not result or not result.strip():
                raise TranslationError(
                    "Translator returned an empty result for non-empty input."
                )
            return result.strip()
        except (LanguageNotSupportedException, NotValidPayload) as e:
            # Not worth retrying — these are input problems, not transient.
            raise TranslationError(
                f"Translation request invalid ({language_name(source_lang_code)} "
                f"-> {language_name(target_lang_code)}): {e}"
            )
        except TranslationNotFound as e:
            last_error = f"No translation found: {e}"
        except Exception as e:
            last_error = f"Translation request failed: {e}"

        if attempt < 2:
            # Fix #8: time.sleep is a blocking call, but this function is
            # always called via asyncio.to_thread() in main.py, which runs
            # it on a worker thread — so blocking here never freezes the
            # event loop. Safe as long as this is never awaited directly.
            time.sleep(2 ** attempt)  # 1s, then 2s backoff

    raise TranslationError(last_error or "Unknown translation failure.")