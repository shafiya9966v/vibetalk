"""
gnani_client.py — reusable wrapper around Prisma (STT) and Timbre (TTS).

Put this in: vibetalk/backend/gnani_client.py

This module does the actual network calls. It returns plain Python values
(strings, bytes) or raises GnaniAPIError with a clear message — it does NOT
print anything itself, so it can be reused by test scripts, the bridge
pipeline, and later the real FastAPI backend without duplicating logic.

Edge cases handled here:
- Unsupported audio file formats (auto-converted via ffmpeg)
- Empty / silent audio (STT returns an empty transcript)
- Missing API key
- 401/403 auth & credit errors
- 429 rate limiting (one retry with backoff)
- 500/503 transient server errors (one retry with backoff)
- Non-JSON / malformed responses
- TTS text that is empty or only whitespace
"""

import os
import shutil
import subprocess
import tempfile
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
BASE_URL = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")

STT_PATH = "/stt/v3"
TTS_PATH = "/api/v1/tts/inference"

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}

# Status codes worth retrying once, with a short backoff.
_RETRYABLE_STATUSES = {429, 500, 503}
_RETRY_BACKOFF_SECONDS = 2


class GnaniAPIError(Exception):
    """Raised for any failure talking to Prisma or Timbre."""

    def __init__(self, message: str, status_code: int | None = None, error_type: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


def _require_api_key() -> str:
    if not API_KEY:
        raise GnaniAPIError(
            "GNANI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    return API_KEY


def _ensure_supported_audio_format(audio_path: str) -> tuple[str, bool]:
    """
    Returns (path_to_use, was_converted). If the input is already a
    supported format, returns it unchanged. Otherwise converts to a
    temporary 16kHz mono WAV via ffmpeg.
    """
    if not os.path.exists(audio_path):
        raise GnaniAPIError(f"Audio file not found: {audio_path}")

    ext = os.path.splitext(audio_path)[1].lower()
    if ext in SUPPORTED_AUDIO_EXTENSIONS:
        return audio_path, False

    if shutil.which("ffmpeg") is None:
        raise GnaniAPIError(
            f"'{ext}' is not a supported format "
            f"({', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}), and ffmpeg "
            "is not installed to convert it. Install ffmpeg and ensure it's "
            "on your PATH."
        )

    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav.close()

    _t0 = time.monotonic()
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", tmp_wav.name],
        capture_output=True,
        text=True,
    )
    print(f"[timing] ffmpeg conversion took {time.monotonic() - _t0:.2f}s")
    if result.returncode != 0:
        os.unlink(tmp_wav.name)
        raise GnaniAPIError(f"ffmpeg conversion failed: {result.stderr[-500:]}")

    return tmp_wav.name, True


# A fresh httpx.post() call opens a brand-new TCP + TLS connection every
# single time — that handshake alone can easily cost several hundred ms to
# over a second depending on network conditions, ON TOP OF the actual
# request. Every call here goes to the same host (api.vachana.ai), so
# reusing one client keeps the connection alive between calls (HTTP
# keep-alive) and skips that handshake after the first request.
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_client = httpx.Client(timeout=_TIMEOUT)


def _post_with_retry(url: str, **kwargs) -> httpx.Response:
    """POST with one retry on 429/500/503, raising on request-level failures."""
    last_resp = None
    for attempt in range(2):
        try:
            resp = _client.post(url, **kwargs)
        except httpx.TimeoutException as e:
            raise GnaniAPIError(f"Request to {url} timed out: {e}")
        except httpx.RequestError as e:
            raise GnaniAPIError(f"Network error reaching {url}: {e}")

        if resp.status_code not in _RETRYABLE_STATUSES:
            return resp

        last_resp = resp
        if attempt == 0:
            time.sleep(_RETRY_BACKOFF_SECONDS)

    return last_resp


def _parse_error(resp: httpx.Response) -> GnaniAPIError:
    try:
        body = resp.json()
        err = body.get("error", {})
        message = err.get("message", str(body))
        error_type = err.get("type", "UNKNOWN")
    except ValueError:
        message = resp.text[:500] or f"HTTP {resp.status_code} with no body"
        error_type = "NON_JSON_RESPONSE"

    if resp.status_code in (401, 403):
        message += " (check API key validity, org status, or remaining credits)"

    return GnaniAPIError(message, status_code=resp.status_code, error_type=error_type)


class TranscriptionResult:
    """
    Richer result than a plain string, since Prisma v3.0 gives us the
    detected language and real timing — both genuinely useful: the
    detected language IS the source language for translation (no manual
    selection needed), and the timing is worth surfacing in the UI/demo
    as proof of real performance, not a mocked response.
    """

    def __init__(self, transcript: str, detected_language_code: str | None, processing_time: float | None):
        self.transcript = transcript
        self.detected_language_code = detected_language_code
        self.processing_time = processing_time

    def __bool__(self):
        return bool(self.transcript)


def transcribe(
    audio_path: str,
    language_code: str = "indic-auto",
    audio_format: str = "transcribe",
    model: str = "prisma-v3.0",
) -> TranscriptionResult:
    """
    Transcribe an audio file with Prisma.

    Defaults to v3.0 with auto-detection (indic-auto) — pass an explicit
    language_code (e.g. "hi-IN") only if you want to force a specific
    language instead of detecting it.

    Returns a TranscriptionResult. transcript may be an EMPTY STRING if no
    speech was detected (not an error — callers should check explicitly,
    or just use `if not result:`). detected_language_code is None if the
    API didn't return one (e.g. older model, or empty transcript).

    Raises GnaniAPIError on any failure (auth, format, network, API error).
    """
    api_key = _require_api_key()
    resolved_path, converted = _ensure_supported_audio_format(audio_path)

    try:
        with open(resolved_path, "rb") as f:
            files = {"audio_file": (os.path.basename(resolved_path), f)}
            data = {"language_code": language_code, "format": audio_format, "model": model}
            headers = {"X-API-Key-ID": api_key}
            _net_t0 = time.monotonic()
            resp = _post_with_retry(
                f"{BASE_URL}{STT_PATH}", headers=headers, data=data, files=files
            )
            print(f"[timing] STT network call (upload+inference+download) took {time.monotonic() - _net_t0:.2f}s")
    finally:
        if converted:
            try:
                os.unlink(resolved_path)
            except OSError:
                pass

    if resp.status_code != 200:
        raise _parse_error(resp)

    try:
        body = resp.json()
    except ValueError:
        raise GnaniAPIError("STT returned a non-JSON 200 response — unexpected.")

    if not body.get("success"):
        raise _parse_error(resp)

    # Empty transcript is a valid, non-error outcome (e.g. silence).
    transcript = (body.get("transcript") or "").strip()
    return TranscriptionResult(
        transcript=transcript,
        detected_language_code=body.get("detected_language_code"),
        processing_time=body.get("processing_time"),
    )


def synthesize(
    text: str,
    voice: str,
    language: str,
    model: str = "timbre-v2.5",
) -> bytes:
    """
    Synthesize speech with Timbre TTS. Returns raw WAV audio bytes.

    Raises GnaniAPIError if text is empty/whitespace-only, or on any API
    failure.
    """
    api_key = _require_api_key()

    if not text or not text.strip():
        raise GnaniAPIError("Cannot synthesize empty text.")

    payload = {
        "text": text,
        "voice": voice,
        "model": model,
        "language": language,
        "speed": 1.0,
        "audio_config": {
            # 16kHz is standard for spoken voice (not music) — noticeably
            # smaller payload than 48kHz with no real quality loss for
            # speech, which speeds up generation + transfer.
            "sample_rate": 16000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }
    headers = {"X-API-Key-ID": api_key, "Content-Type": "application/json"}

    resp = _post_with_retry(f"{BASE_URL}{TTS_PATH}", headers=headers, json=payload)

    content_type = resp.headers.get("content-type", "")
    if resp.status_code == 200 and "audio" in content_type:
        return resp.content

    raise _parse_error(resp)