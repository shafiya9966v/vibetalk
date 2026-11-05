"""
test_v3_on_rest.py — quick one-off check: does the STT REST endpoint
accept model=prisma-v3.0, language_code=indic-auto, format=codemix?

Put this in: vibetalk/backend/test_v3_on_rest.py (temporary/throwaway,
not part of the main pipeline)

Usage:
    python test_v3_on_rest.py "telugu+english.mp4"
"""

import os
import sys
import json

import httpx
from dotenv import load_dotenv

# Reuse the same format-conversion logic every other script in this
# project already uses, instead of duplicating it (and forgetting it,
# like this script just did).
from gnani_client import _ensure_supported_audio_format

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
BASE_URL = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_v3_on_rest.py audio_file")
        sys.exit(1)

    audio_path, converted = _ensure_supported_audio_format(sys.argv[1])
    print("Checking the FULL raw response for model=prisma-v3.0, indic-auto, transcribe ...")

    with open(audio_path, "rb") as f:
        files = {"audio_file": (os.path.basename(audio_path), f)}
        data = {
            "language_code": "indic-auto",
            "format": "transcribe",  # clean single-script text, best for translation
            "model": "prisma-v3.0",
        }
        headers = {"X-API-Key-ID": API_KEY}
        resp = httpx.post(f"{BASE_URL}/stt/v3", headers=headers, data=data, files=files, timeout=30.0)

    if converted:
        os.unlink(audio_path)

    print(f"Status: {resp.status_code}")
    print("Full response JSON:")
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    print("\n-> Look for a 'detected_language_code' field above. If it's there,")
    print("   we build the pipeline around it. If not, we need another plan")
    print("   for knowing which language was actually spoken.")