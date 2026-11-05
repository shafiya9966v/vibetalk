"""
Step 2 — standalone Prisma (STT) test script, using the real API spec.

Endpoint: POST https://api.vachana.ai/stt/v3
Auth:     X-API-Key-ID header
Body:     multipart/form-data

Usage:
    python test_prisma_stt.py path/to/audio.wav [language_code]
    python test_prisma_stt.py english.mp4 [language_code]

language_code defaults to en-IN. Supported codes:
    bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, pa-IN, ta-IN, te-IN

Audio must be <= 60 seconds (ideally <= 30s).
Supported native formats: WAV, MP3, OGG, FLAC, AAC, M4A.
MP4 files are automatically converted to 16kHz mono WAV via FFmpeg before sending.
"""

import os
import sys
import json
import mimetypes
import tempfile
import subprocess
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
BASE_URL = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")
STT_PATH = "/stt/v3"

# Native audio formats directly accepted by Gnani STT v3
NATIVE_AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}


def prepare_audio_file(audio_path: str) -> tuple[str, bool]:
    """
    Checks if the audio format is directly supported by the STT API.
    If it's an MP4 or other container, converts audio to 16kHz mono WAV using ffmpeg.
    Returns (path_to_send, is_temporary).
    """
    ext = Path(audio_path).suffix.lower()

    if ext in NATIVE_AUDIO_EXTS:
        return audio_path, False

    # For MP4 or any non-native container, extract/convert to 16kHz mono WAV
    print(f"Format '{ext}' detected. Auto-converting '{os.path.basename(audio_path)}' to 16kHz mono WAV via FFmpeg...")

    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_wav_path = temp_wav.name
    temp_wav.close()

    cmd = [
        "ffmpeg",
        "-y",
        "-i", audio_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        temp_wav_path,
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print(f"  [OK] Successfully converted to temporary WAV: {os.path.basename(temp_wav_path)}")
        return temp_wav_path, True
    except FileNotFoundError:
        if os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)
        print("ERROR: FFmpeg is not installed or not in PATH.")
        print("Please install FFmpeg or convert your audio file to WAV/MP3/M4A manually.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        if os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)
        err = e.stderr.decode("utf-8", errors="ignore")
        print(f"ERROR: Audio conversion failed: {err}")
        sys.exit(1)


def transcribe(audio_path: str, language_code: str = "en-IN") -> None:
    if not API_KEY:
        print("ERROR: GNANI_API_KEY not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    if not os.path.exists(audio_path):
        print(f"ERROR: file not found: {audio_path}")
        sys.exit(1)

    actual_path, is_temp = prepare_audio_file(audio_path)

    url = f"{BASE_URL}{STT_PATH}"
    headers = {"X-API-Key-ID": API_KEY}

    data = {
        # "transcribe" enables ITN (numbers/currency/dates formatted nicely).
        # Use "verbatim" if you want raw spoken-form output instead.
        "language_code": language_code,
        "format": "transcribe",
    }

    # Determine proper mime type
    mime_type, _ = mimetypes.guess_type(actual_path)
    if not mime_type or not mime_type.startswith("audio/"):
        mime_type = "audio/wav"

    try:
        with open(actual_path, "rb") as f:
            files = {"audio_file": (os.path.basename(actual_path), f, mime_type)}
            print(f"POST {url}  (language_code={language_code}, file={os.path.basename(actual_path)})")
            try:
                resp = httpx.post(url, headers=headers, data=data, files=files, timeout=30.0)
            except httpx.RequestError as e:
                print(f"Request failed before reaching the server: {e}")
                sys.exit(1)

        print(f"Status: {resp.status_code}")

        try:
            body = resp.json()
        except ValueError:
            print("Response was not JSON:")
            print(resp.text[:1000])
            return

        if resp.status_code == 200 and body.get("success"):
            print(f"Transcript: {body['transcript']}")
            print(f"request_id: {body.get('request_id')}")
        else:
            # Matches the documented error shape:
            # {"success": false, "error": {"type": "...", "message": "..."}}
            err = body.get("error", {})
            print(f"Error [{err.get('type', 'UNKNOWN')}]: {err.get('message', body)}")
            if resp.status_code == 403:
                print("Check: API key validity, org status, or remaining credits.")
    finally:
        if is_temp and os.path.exists(actual_path):
            try:
                os.remove(actual_path)
            except OSError:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python test_prisma_stt.py path/to/audio [language_code]")
        print("e.g.:  python test_prisma_stt.py english.mp4 en-IN")
        print("       python test_prisma_stt.py sample.wav te-IN")
        print("\nSupported formats:")
        print("  - Native: .wav, .mp3, .ogg, .flac, .aac, .m4a")
        print("  - Auto-converted: .mp4 (converted to 16kHz mono WAV on the fly)")
        sys.exit(0)
    lang = sys.argv[2] if len(sys.argv) > 2 else "en-IN"
    transcribe(sys.argv[1], lang)
