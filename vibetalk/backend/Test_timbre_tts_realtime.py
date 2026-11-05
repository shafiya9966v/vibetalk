"""
test_timbre_tts_realtime.py — standalone validation of Timbre TTS
Realtime (WebSocket streaming).

Put this in: vibetalk/backend/test_timbre_tts_realtime.py

SETUP REQUIRED:
    pip install websockets

Usage:
    python test_timbre_tts_realtime.py "Namaste, aap kaise hain" Nalini hi-IN

Saves streamed audio chunks to output_realtime.wav as they arrive, and
prints timing for the FIRST chunk vs the LAST chunk — that gap is the real
number that matters for "does streaming actually feel faster," since the
first chunk is what determines how soon playback could start in the real
app, not the total time.
"""

import asyncio
import base64
import json
import os
import sys
import time

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
WS_URL = "wss://api.vachana.ai/api/v1/tts"


async def run(text: str, voice: str, language: str, out_path: str = "output_realtime.wav") -> None:
    if not API_KEY:
        print("ERROR: GNANI_API_KEY not set in .env")
        sys.exit(1)

    headers = {"X-API-Key-ID": API_KEY}
    print(f"Connecting to {WS_URL} ...")

    try:
        connect_kwargs = {"additional_headers": headers}
        try:
            ws = await websockets.connect(WS_URL, **connect_kwargs)
        except TypeError:
            ws = await websockets.connect(WS_URL, extra_headers=headers)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    async with ws:
        request = {
            "text": text,
            "voice": voice,
            "model": "timbre-v2.5",
            "language": language,
            "speed": 1.0,
            "audio_config": {
                "sample_rate": 16000,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "wav",
            },
        }
        t_start = time.monotonic()
        await ws.send(json.dumps(request))
        print(f"Sent synthesis request: {text!r} (voice={voice}, language={language})")

        chunks = []
        t_first_chunk = None
        chunk_count = 0

        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get("type")

            if kind == "start":
                print(f"[start] {msg.get('message')} request_id={msg.get('request_id')}")
            elif kind == "audio":
                data = msg.get("data", {})
                if t_first_chunk is None:
                    t_first_chunk = time.monotonic()
                    print(f"[audio] first chunk arrived after {t_first_chunk - t_start:.2f}s")
                chunk_count += 1
                audio_bytes = base64.b64decode(data.get("audio", ""))
                chunks.append(audio_bytes)
                if data.get("is_final"):
                    break
            elif kind == "complete":
                print("[complete] synthesis finished")
                break
            elif kind == "error":
                print(f"[error] {msg.get('message')}")
                return
            else:
                print(f"[{kind}] {msg}")

        t_end = time.monotonic()

    full_audio = b"".join(chunks)
    with open(out_path, "wb") as f:
        f.write(full_audio)

    print(f"\nSaved {len(full_audio)} bytes across {chunk_count} chunks to {out_path}")
    if t_first_chunk:
        print(f"Time to FIRST audio chunk: {t_first_chunk - t_start:.2f}s  <- this is what matters for perceived speed")
    print(f"Time to LAST chunk (total): {t_end - t_start:.2f}s")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python test_timbre_tts_realtime.py "text" [voice] [language]')
        sys.exit(1)
    text_arg = sys.argv[1]
    voice_arg = sys.argv[2] if len(sys.argv) > 2 else "Nalini"
    language_arg = sys.argv[3] if len(sys.argv) > 3 else "hi-IN"
    asyncio.run(run(text_arg, voice_arg, language_arg))