"""
test_prisma_stt_realtime.py — standalone validation of Prisma v3.0 STT
Realtime (WebSocket), including indic-auto language detection and
codemix output format.

Put this in: vibetalk/backend/test_prisma_stt_realtime.py

SETUP REQUIRED:
    pip install websockets

This does NOT touch the FastAPI backend or gnani_client.py yet — it's
standalone validation, same philosophy as Step 2's REST test scripts.
Once this proves the real message flow works, we wire it into the app.

Usage:
    python test_prisma_stt_realtime.py audio.mp4
    python test_prisma_stt_realtime.py audio.mp4 hi-IN,en-IN

    If no language_code is given, defaults to indic-auto (model detects
    the language itself — this is the new v3.0 capability).

What this proves before we build anything on top of it:
- The WebSocket handshake + headers work with your API key
- indic-auto actually detects language correctly on your real audio
- codemix format output looks the way we expect (English words in Latin
  script, rest in native script)
- Real per-segment latency, so we know if this is actually faster than
  REST or just different
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GNANI_API_KEY")
WS_URL = "wss://api.vachana.ai/stt/v3/stream"

SAMPLE_RATE = 16000
FRAME_BYTES = 1024  # 512 samples * 2 bytes, per the spec
FRAME_SECONDS = 1024 / 2 / SAMPLE_RATE  # 32ms at 16kHz


def extract_raw_pcm(audio_path: str) -> bytes:
    """
    Converts any input audio file to raw 16-bit PCM, 16kHz, mono, no
    header — the exact format the Realtime endpoint requires (no WAV/MP3
    containers accepted mid-stream).
    """
    if shutil.which("ffmpeg") is None:
        print("ERROR: ffmpeg is required to prepare audio for streaming. Install it first.")
        sys.exit(1)

    tmp_pcm = tempfile.NamedTemporaryFile(suffix=".pcm", delete=False)
    tmp_pcm.close()

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-f", "s16le",      # raw signed 16-bit little-endian
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            tmp_pcm.name,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ffmpeg conversion failed:\n{result.stderr[-500:]}")
        sys.exit(1)

    with open(tmp_pcm.name, "rb") as f:
        data = f.read()
    os.unlink(tmp_pcm.name)
    return data


async def run(audio_path: str, language_code: str) -> None:
    if not API_KEY:
        print("ERROR: GNANI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Converting '{audio_path}' to raw PCM...")
    pcm_data = extract_raw_pcm(audio_path)

    headers = {
        "x-api-key-id": API_KEY,
        "model": "prisma-v3.0",
        "language_code": language_code,
        "format": "codemix",
        "x-sample-rate": str(SAMPLE_RATE),
    }

    print(f"Connecting to {WS_URL} (language_code={language_code}, format=codemix)...")

    try:
        connect_kwargs = {"additional_headers": headers}
        try:
            ws = await websockets.connect(WS_URL, **connect_kwargs)
        except TypeError:
            # Older websockets versions use `extra_headers` instead.
            ws = await websockets.connect(WS_URL, extra_headers=headers)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    async with ws:
        transcripts_received = asyncio.Event()

        async def sender():
            # Send audio in exact 1024-byte frames at real-time cadence,
            # per the spec — no bursting.
            for i in range(0, len(pcm_data), FRAME_BYTES):
                frame = pcm_data[i:i + FRAME_BYTES]
                if len(frame) < FRAME_BYTES:
                    frame = frame + b"\x00" * (FRAME_BYTES - len(frame))
                await ws.send(frame)
                await asyncio.sleep(FRAME_SECONDS)

            # ~1s of silence so the final segment is recognized as complete,
            # then commit to force-close it immediately rather than waiting
            # on the idle timeout.
            silence_frames = int(1.0 / FRAME_SECONDS)
            for _ in range(silence_frames):
                await ws.send(b"\x00" * FRAME_BYTES)
                await asyncio.sleep(FRAME_SECONDS)

            await ws.send(json.dumps({"type": "commit"}))
            print("(finished sending audio, sent commit)")

        async def receiver():
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")

                if kind == "connected":
                    print(f"[connected] {msg.get('message')} config={msg.get('config')}")
                elif kind == "speech_start":
                    print("[speech_start]")
                elif kind == "processing":
                    print("[processing] (speech segment detected, transcribing...)")
                elif kind == "transcript":
                    lang = msg.get("detected_language_code", "n/a")
                    latency = msg.get("latency")
                    print(f"[transcript] ({lang}, {latency}ms) {msg.get('text')}")
                    transcripts_received.set()
                elif kind == "error":
                    print(f"[error] {msg.get('message')}")
                else:
                    print(f"[{kind}] {msg}")

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())

        try:
            await asyncio.wait_for(send_task, timeout=60)
            # Give the server a few seconds after commit to deliver the
            # final transcript before we close.
            await asyncio.wait_for(transcripts_received.wait(), timeout=10)
        except asyncio.TimeoutError:
            print("(timed out waiting for final transcript — closing anyway)")
        finally:
            recv_task.cancel()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_prisma_stt_realtime.py audio.mp4 [language_code]")
        print("e.g.:  python test_prisma_stt_realtime.py telugu+english.mp4")
        print("       python test_prisma_stt_realtime.py telugu+english.mp4 indic-auto")
        sys.exit(1)
    lang = sys.argv[2] if len(sys.argv) > 2 else "indic-auto"
    asyncio.run(run(sys.argv[1], lang))