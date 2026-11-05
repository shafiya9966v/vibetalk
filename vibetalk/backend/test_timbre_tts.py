"""
Step 2 — standalone Timbre (TTS) test script, using the real API spec.

Endpoint: POST https://api.vachana.ai/api/v1/tts/inference
Auth:     X-API-Key-ID header
Body:     application/json
Response: raw binary audio (audio/wav by default) on 200,
          JSON error body on 400/403/429/500/503.

Usage:
    python test_timbre_tts.py "Namaste, nenu VibeTalk ni test chestunna." [voice] [language]

Defaults: voice=Nalini (Hindi), language=hi-IN
For Hinglish try: language=hi-en
For Telugu try:   language=te-IN  (pick a Telugu-preferred voice from the
                   Timbre v2.5 voice catalog — Nalini defaults to Hindi)
"""

import os
import sys
import json

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
BASE_URL = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")
TTS_PATH = "/api/v1/tts/inference"


def synthesize(
    text: str,
    voice: str = "Nalini",
    language: str = "hi-IN",
    out_path: str = "output.wav",
) -> None:
    if not API_KEY:
        print("ERROR: GNANI_API_KEY not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    url = f"{BASE_URL}{TTS_PATH}"
    headers = {
        "X-API-Key-ID": API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "voice": voice,
        "model": "timbre-v2.5",
        "language": language,
        "speed": 1.0,
        "audio_config": {
            "sample_rate": 48000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }

    print(f"POST {url}  (voice={voice}, language={language})")

    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
    except httpx.RequestError as e:
        print(f"Request failed before reaching the server: {e}")
        sys.exit(1)

    print(f"Status: {resp.status_code}")
    content_type = resp.headers.get("content-type", "")

    if resp.status_code == 200 and "audio" in content_type:
        with open(out_path, "wb") as f:
            f.write(resp.content)
        print(f"Saved audio to {out_path} — play it to check voice quality.")
        return

    # Error path: documented shape is {"success": false, "error": {"type", "message"}}
    try:
        body = resp.json()
        err = body.get("error", {})
        print(f"Error [{err.get('type', 'UNKNOWN')}]: {err.get('message', body)}")
        if resp.status_code == 403:
            print("Check: API key validity, org status, or remaining credits.")
    except ValueError:
        print("Non-JSON, non-audio response:")
        print(resp.text[:1000])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python test_timbre_tts.py "text to speak" [voice] [language]')
        sys.exit(1)
    text_arg = sys.argv[1]
    voice_arg = sys.argv[2] if len(sys.argv) > 2 else "Nalini"
    language_arg = sys.argv[3] if len(sys.argv) > 3 else "hi-IN"
    synthesize(text_arg, voice_arg, language_arg)
