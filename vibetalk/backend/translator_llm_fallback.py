"""
translator.py — translates text from one language to another using an LLM.

Put this in: vibetalk/backend/translator.py

This is intentionally provider-agnostic: it calls any OpenAI-compatible
chat completions endpoint (OpenAI, Groq, Together, OpenRouter, a local
Ollama server, etc. all support this same request shape). Point it at
whichever provider you choose by setting these in your .env:

    LLM_API_KEY=your-key-here
    LLM_BASE_URL=https://api.groq.com/openai/v1      # example — pick your provider
    LLM_MODEL=llama-3.1-8b-instant                     # example — pick your model

Gnani's APIs only cover STT/TTS, not text generation/translation — this
piece is explicitly something you choose and plug in yourself (see the
build spec, section 6).

Edge cases handled:
- Empty / whitespace-only input -> returns "" without calling the API
- source_lang == target_lang -> returns the original text unchanged,
  no API call made (saves cost/latency, and avoids the LLM "translating"
  text that's already correct)
- Missing API key / base URL -> raises a clear TranslationError
- Network failure -> one retry, then raises TranslationError
- Non-200 response -> raises TranslationError with the response body
- LLM adds extra commentary around the translation -> the prompt
  instructs it to return ONLY the translated text, and we strip
  surrounding whitespace/quotes defensively
"""

import os
import time

import httpx
from dotenv import load_dotenv

from language_utils import language_name

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")


class TranslationError(Exception):
    pass


def _build_prompt(text: str, source_lang: str, target_lang: str) -> str:
    return (
        f"Translate the following {source_lang} text into natural, "
        f"conversational spoken {target_lang}. Preserve the meaning and "
        f"tone — do not make it more formal than the original. "
        f"Output ONLY the translated text, with no explanation, no quotes, "
        f"and no original text included.\n\n"
        f"Text to translate:\n{text}"
    )


def translate_text(text: str, source_lang_code: str, target_lang_code: str) -> str:
    """
    Translate `text` from source_lang_code to target_lang_code.

    Returns the translated text. If source_lang_code == target_lang_code,
    returns the input unchanged without making an API call.
    """
    if not text or not text.strip():
        return ""

    if source_lang_code == target_lang_code:
        return text.strip()

    if not LLM_API_KEY or not LLM_BASE_URL or not LLM_MODEL:
        raise TranslationError(
            "LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL must all be set in "
            ".env before translation can run. See the comment at the top "
            "of translator.py for how to configure a provider."
        )

    source_name = language_name(source_lang_code)
    target_name = language_name(target_lang_code)
    prompt = _build_prompt(text, source_name, target_name)

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

    last_error = None
    for attempt in range(2):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
        except httpx.RequestError as e:
            last_error = f"Network error reaching {url}: {e}"
            if attempt == 0:
                time.sleep(2)
            continue

        if resp.status_code == 200:
            try:
                data = resp.json()
                translated = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                raise TranslationError(
                    f"Unexpected response shape from LLM provider: {e}. "
                    f"Raw response: {resp.text[:500]}"
                )
            return translated.strip().strip('"').strip("'")

        last_error = f"LLM provider returned {resp.status_code}: {resp.text[:500]}"
        if resp.status_code in (429, 500, 503) and attempt == 0:
            time.sleep(2)
            continue
        break

    raise TranslationError(last_error or "Unknown translation failure.")