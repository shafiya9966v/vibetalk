"""
VibeTalk backend — Step 3b: real /api/bridge endpoint, plus the realtime
WebSocket relays for STT and TTS.

/api/bridge: REST-based pipeline (gnani_client.py + translator.py +
language_utils.py), file-upload based. Still works, kept as a fallback.

/ws/stt: realtime STT relay, one connection per conversation session.

/ws/tts: realtime TTS relay, using ONE persistent connection to Gnani
opened at server startup and reused across every request — this is what
actually delivers low first-chunk latency, since the ~1s TLS handshake
cost is paid once at startup, not on every synthesis call.
"""

import base64
import os
import tempfile
import time
from datetime import datetime, timezone

import asyncio
import json as jsonlib

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
API_KEY = os.getenv("GNANI_API_KEY")

from gnani_client import transcribe, synthesize, GnaniAPIError
from translator import translate_text, TranslationError
from language_utils import (
    is_supported_stt_language,
    is_supported_tts_language,
    language_name,
    voice_for,
    STT_LANGUAGES,
    TTS_LANGUAGES,
)

app = FastAPI(title="VibeTalk Backend", version="0.3.0")

# Allow localhost for dev, and any origin for production deployments.
# If you want to lock down production, replace "*" with your real domain.
_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",   # vite preview
    "http://127.0.0.1:4173",
]
_CORS_ORIGIN_REGEX = r"https://.*"  # allow any https origin in production

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reject absurdly large uploads before even touching them (~10 MB is
# generous for a 30-60s voice clip; prevents accidental huge-file abuse).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

class PingResponse(BaseModel):
    message: str
    timestamp: str


class BridgeResponse(BaseModel):
    transcript: str
    translated_text: str
    audio_base64: str | None  # None if no speech was detected
    detected_source_lang: str | None  # auto-detected by Prisma v3.0
    target_lang: str
    stt_latency_s: float | None = None  # real timing, shown in UI as proof of live processing
    message: str | None = None  # set when there's nothing to say (e.g. silence)


@app.get("/")
def root():
    return {"status": "VibeTalk backend is running"}


@app.get("/api/ping", response_model=PingResponse)
def ping():
    return PingResponse(
        message="pong from VibeTalk backend",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/languages")
def list_languages():
    """
    Lets the frontend build language dropdowns without hardcoding codes
    on the client side.
    """
    return {
        "stt_languages": STT_LANGUAGES,
        "tts_languages": {code: language_name(code) for code in sorted(TTS_LANGUAGES)},
    }


def _gnani_error_to_http(e: GnaniAPIError) -> HTTPException:
    """
    Maps a Gnani API failure to an appropriate HTTP response for our
    frontend. We don't just forward Gnani's status code as-is — from our
    frontend's point of view, any upstream failure is "the service we
    depend on had a problem," which is a 502, except rate limiting (429),
    which is worth passing through as-is so the frontend can back off.
    """
    if e.status_code == 429:
        return HTTPException(status_code=429, detail=str(e))
    return HTTPException(status_code=502, detail=f"Upstream Gnani API error: {e}")


@app.post("/api/bridge", response_model=BridgeResponse)
async def bridge(
    audio_file: UploadFile = File(...),
    target_lang: str = Form(...),
    source_lang: str = Form("indic-auto"),
):
    """
    One walkie-talkie turn of Bridge Mode (REST path, kept as a fallback
    alongside the realtime /ws/stt + /ws/tts relays):
        uploaded audio -> transcript, with the spoken language AUTO-DETECTED
        by Prisma v3.0 (source_lang defaults to "indic-auto" — the speaker
        does not need to declare what language they're speaking)
        -> translated text (target_lang)
        -> synthesized audio (target_lang), returned as base64

    source_lang can still be passed explicitly (e.g. "te-IN") to force a
    specific language instead of auto-detecting — useful as a fallback if
    detection is ever wrong on noisy audio, but not required for normal use.

    Edge cases handled:
    - Unsupported language codes -> 400, before any API call is made
    - Oversized upload -> 400
    - Empty/unreadable audio file -> 400
    - Silence / no speech detected -> 200 with audio_base64=None and a
      `message` explaining why, NOT an error (nothing went wrong)
    - Any Prisma/Timbre failure -> 502 (or 429 passthrough for rate limits)
    - Translation failure -> 502, but only after STT succeeded, so the
      transcript is still returned to the frontend via the error detail
      path (see exception handling below) rather than silently lost
    - Unsupported/unconvertible audio format -> 400, surfaced clearly
      rather than a generic 502
    - Auto-detect returning no usable language code -> falls back to
      en-IN for translation purposes rather than crashing, with a note
    """
    if not is_supported_stt_language(source_lang):
        raise HTTPException(
            status_code=400,
            detail=f"'{source_lang}' is not a supported source language. "
            f"Supported: indic-auto (default), {', '.join(sorted(STT_LANGUAGES.keys()))}",
        )
    if not is_supported_tts_language(target_lang):
        raise HTTPException(
            status_code=400,
            detail=f"'{target_lang}' is not a supported target language. "
            f"Supported: {', '.join(sorted(TTS_LANGUAGES))}",
        )

    # Save the upload to a temp file — gnani_client's functions work off
    # file paths (and reuse the same ffmpeg auto-conversion logic already
    # validated in Step 3a), not in-memory streams.
    suffix = os.path.splitext(audio_file.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        contents = await audio_file.read()
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Audio file too large ({len(contents)} bytes, "
                f"max {MAX_UPLOAD_BYTES}).",
            )
        tmp.write(contents)
        tmp.close()

        # --- Step 1: transcribe (defaults to v3.0 + indic-auto) ---
        t0 = time.monotonic()
        try:
            stt_result = transcribe(tmp.name, language_code=source_lang)
        except GnaniAPIError as e:
            raise _gnani_error_to_http(e)
        stt_elapsed = time.monotonic() - t0
        print(f"[timing] STT took {stt_elapsed:.2f}s (model-reported: {stt_result.processing_time})")

        if not stt_result.transcript:
            return BridgeResponse(
                transcript="",
                translated_text="",
                audio_base64=None,
                detected_source_lang=stt_result.detected_language_code,
                target_lang=target_lang,
                message="No speech detected in the audio. Try a clearer or louder recording.",
            )

        # The actual spoken language, for translation purposes, is whatever
        # Prisma detected — NOT the "indic-auto" placeholder we may have
        # sent as source_lang. Fall back to English only in the unlikely
        # case detection returned nothing usable.
        detected_lang = stt_result.detected_language_code or "en-IN"

        # --- Step 2: translate (skipped if detected == target) ---
        t1 = time.monotonic()
        try:
            translated_text = translate_text(stt_result.transcript, detected_lang, target_lang)
        except TranslationError as e:
            # Preserve the transcript in the error so the frontend can
            # still show *something* useful instead of just "it failed."
            raise HTTPException(
                status_code=502,
                detail=f"Translation failed: {e} (transcript was: {stt_result.transcript!r})",
            )

        print(f"[timing] Translation took {time.monotonic() - t1:.2f}s")

        # --- Step 3: synthesize ---
        t2 = time.monotonic()
        voice = voice_for(target_lang)
        try:
            audio_bytes = synthesize(translated_text, voice=voice, language=target_lang)
        except GnaniAPIError as e:
            # Same principle: don't lose the translated text just because
            # TTS failed — surface it in the error detail.
            raise HTTPException(
                status_code=502,
                detail=f"Speech synthesis failed: {e} "
                f"(translated text was: {translated_text!r})",
            )

        print(f"[timing] TTS took {time.monotonic() - t2:.2f}s")
        print(f"[timing] TOTAL pipeline: {time.monotonic() - t0:.2f}s")

        return BridgeResponse(
            transcript=stt_result.transcript,
            translated_text=translated_text,
            audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
            detected_source_lang=detected_lang,
            target_lang=target_lang,
            stt_latency_s=round(stt_elapsed, 2),
        )

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


GNANI_STT_WS_URL = "wss://api.vachana.ai/stt/v3/stream"


@app.websocket("/ws/stt")
async def ws_stt_relay(websocket: WebSocket):
    """
    Realtime STT relay, one connection per conversation session (opened
    when the frontend connects, held open across multiple utterances,
    closed when the frontend disconnects).

    Protocol with the frontend:
    - Frontend connects, then sends ONE JSON text message first:
          {"language_code": "indic-auto"}   (or a specific code)
    - Frontend then sends binary PCM frames (1024 bytes each, 16kHz,
      16-bit mono) continuously while the person speaks.
    - Frontend sends {"type": "commit"} as a JSON text message when the
      person releases the mic, to force-close the utterance immediately
      instead of waiting on silence detection.
    - Backend forwards every Gnani message back to the frontend as-is
      (connected / processing / transcript / error), so the frontend can
      show real state and we can debug this relay layer in isolation.

    Edge cases handled:
    - Frontend disconnects mid-session -> Gnani connection is closed
      cleanly, no orphaned connection left open
    - Gnani connection fails or errors -> frontend is told via a JSON
      error message, not silently dropped
    - Malformed first message (not valid JSON / missing language_code)
      -> defaults to indic-auto rather than crashing the connection
    """
    await websocket.accept()

    # First message from the frontend: config.
    try:
        first_msg = await websocket.receive_text()
        config = jsonlib.loads(first_msg)
    except Exception:
        config = {}
    language_code = config.get("language_code", "indic-auto")

    headers = {
        "x-api-key-id": API_KEY,
        "model": "prisma-v3.0",
        "language_code": language_code,
        "format": "transcribe",
        "x-sample-rate": "16000",
    }

    try:
        connect_kwargs = {"additional_headers": headers}
        try:
            gnani_ws = await websockets.connect(GNANI_STT_WS_URL, **connect_kwargs)
        except TypeError:
            gnani_ws = await websockets.connect(GNANI_STT_WS_URL, extra_headers=headers)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Could not connect to Gnani STT: {e}"})
        await websocket.close()
        return

    async def browser_to_gnani():
        """Relay audio frames and control messages FROM the browser TO Gnani."""
        try:
            while True:
                message = await websocket.receive()
                if "bytes" in message and message["bytes"] is not None:
                    await gnani_ws.send(message["bytes"])
                elif "text" in message and message["text"] is not None:
                    # Pass control frames (e.g. {"type": "commit"}) straight through.
                    await gnani_ws.send(message["text"])
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[ws/stt] browser_to_gnani error: {e}")

    async def gnani_to_browser():
        """Relay every message FROM Gnani back TO the browser, unmodified."""
        try:
            async for raw in gnani_ws:
                await websocket.send_text(raw)
        except Exception as e:
            print(f"[ws/stt] gnani_to_browser error: {e}")

    try:
        await asyncio.gather(browser_to_gnani(), gnani_to_browser())
    finally:
        try:
            await gnani_ws.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


GNANI_TTS_WS_URL = "wss://api.vachana.ai/api/v1/tts"


@app.websocket("/ws/tts")
async def ws_tts_relay(websocket: WebSocket):
    """
    Realtime TTS relay — a fresh connection to Gnani per request, opened
    and closed within this one call. Simple and predictable.

    (We tried reusing one persistent connection across requests, including
    a background-reconnect-after-each-use variant, since Gnani's docs say
    connections support sequential reuse. In testing, reuse under rapid
    back-to-back requests produced corrupted responses — 0 audio chunks,
    or one response's audio bleeding into the next — and the
    background-reconnect version degraded further under repeated clicks,
    likely from overlapping reconnect attempts competing with each other.
    Given this is a newly launched API with undocumented edge cases here,
    we're using the simple, reliable approach instead. The Realtime
    endpoint still beats plain REST even without reuse, because audio
    streams in chunks — playback can start on the first chunk instead of
    waiting for the whole file, which REST requires.)

    Protocol with the frontend:
    - Frontend sends ONE JSON message: {"text", "voice", "model", "language", ...}
    - Backend relays start / audio / complete / error messages back as-is
    """
    await websocket.accept()

    try:
        first_msg = await websocket.receive_text()
        request = jsonlib.loads(first_msg)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Invalid request: {e}"})
        await websocket.close()
        return

    headers = {"X-API-Key-ID": API_KEY}
    try:
        connect_kwargs = {"additional_headers": headers}
        try:
            gnani_ws = await websockets.connect(GNANI_TTS_WS_URL, **connect_kwargs)
        except TypeError:
            gnani_ws = await websockets.connect(GNANI_TTS_WS_URL, extra_headers=headers)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Could not connect to Gnani TTS: {e}"})
        await websocket.close()
        return

    try:
        await gnani_ws.send(jsonlib.dumps(request))
        async for raw in gnani_ws:
            await websocket.send_text(raw)
            msg = jsonlib.loads(raw)
            if msg.get("type") in ("complete", "error"):
                break
    except Exception as e:
        print(f"[ws/tts] relay error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await gnani_ws.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/bridge")
async def ws_bridge(websocket: WebSocket):
    """
    Combined STT → translate → TTS pipeline over a single WebSocket.

    KEY CHANGE (lazy STT): Gnani STT WebSocket is opened PER TURN, not at
    session start. This means:
    - No Gnani connection is held open while the user is idle between turns.
    - Hot-reloads / page refreshes don't leak persistent Gnani connections.
    - The "20 active connections" quota is only consumed while actively recording.

    Protocol (unchanged from client's perspective):
      Browser → backend: binary PCM frames while speaking
      Browser → backend: {"type": "commit"} when done speaking
      Backend → browser: {"type": "transcript", "text": "...", "detected_language_code": "..."}
      Backend → browser: {"type": "translated", "text": "..."}
      Backend → browser: {"type": "audio", "data": {...}}  (one per TTS chunk)
      Backend → browser: {"type": "turn_complete"}
      Backend → browser: {"type": "no_speech"}   (if silence)
      Backend → browser: {"type": "error", "stage": "...", "message": "..."}
    """
    await websocket.accept()

    try:
        first_msg = await websocket.receive_text()
        config = jsonlib.loads(first_msg)
    except Exception:
        config = {}
    target_lang = config.get("target_lang", "en-IN")

    stt_headers = {
        "x-api-key-id": API_KEY,
        "model": "prisma-v3.0",
        "language_code": "indic-auto",
        "format": "transcribe",
        "x-sample-rate": "16000",
    }

    async def open_stt_ws():
        """Open a fresh Gnani STT WebSocket for one recording turn."""
        try:
            return await websockets.connect(GNANI_STT_WS_URL, additional_headers=stt_headers)
        except TypeError:
            return await websockets.connect(GNANI_STT_WS_URL, extra_headers=stt_headers)

    # ============================================================
# MINIMAL PATCH — only replace the handle_one_turn function.
# Everything else in your file (ws_stt_relay, ws_tts_relay, the main
# while loop, feed_audio_to_stt, collect_transcript) stays EXACTLY as
# you already have it. Do not touch anything else.
# ============================================================

# Find this function inside ws_bridge and REPLACE it entirely with the
# version below:

    async def handle_one_turn(transcript_text: str, detected_lang: str):
        """Translate → TTS for one completed utterance, streaming results back."""
        # Fix #1: detect same-language and tell the frontend so it can show
        # the right note instead of treating it as a normal translation.
        same_language = (detected_lang == target_lang) or (
            detected_lang == "hi-en" and target_lang == "hi-IN"
        )

        await websocket.send_json({
            "type": "transcript",
            "text": transcript_text,
            "detected_language_code": detected_lang,
        })

        # Start opening the TTS connection at the same time translation runs.
        # asyncio.to_thread runs the blocking translate_text on a worker thread
        # so the TTS connection can proceed concurrently.
        tts_headers = {"X-API-Key-ID": API_KEY}

        async def _connect_tts():
            try:
                return await websockets.connect(GNANI_TTS_WS_URL, additional_headers=tts_headers)
            except TypeError:
                return await websockets.connect(GNANI_TTS_WS_URL, extra_headers=tts_headers)

        translate_task = asyncio.create_task(
            asyncio.to_thread(translate_text, transcript_text, detected_lang or "en-IN", target_lang)
        )
        tts_connect_task = asyncio.create_task(_connect_tts())

        try:
            translated = await translate_task
        except TranslationError as e:
            tts_connect_task.cancel()
            await websocket.send_json({"type": "error", "stage": "translate", "message": str(e)})
            return

        # Fix #1: include same_language flag so the frontend can display the
        # "same language — no translation needed" note correctly.
        await websocket.send_json({
            "type": "translated",
            "text": translated,
            "same_language": same_language,
        })

        # By this point the TTS connection has likely already finished
        # connecting (it's been running in the background this whole time),
        # so this await usually returns instantly instead of waiting ~1s.
        try:
            tts_ws = await tts_connect_task
        except Exception as e:
            await websocket.send_json({"type": "error", "stage": "tts", "message": f"Could not connect to Gnani TTS: {e}"})
            return

        try:
            voice = voice_for(target_lang)
            await tts_ws.send(jsonlib.dumps({
                "text": translated,
                "voice": voice,
                "model": "timbre-v2.5",
                "language": target_lang,
                "speed": 1.0,
                "audio_config": {
                    "sample_rate": 16000, "num_channels": 1,
                    "sample_width": 2, "encoding": "linear_pcm", "container": "wav",
                },
            }))
            # Fix #11: 30-second timeout on TTS streaming — if Gnani sends
            # "start" but never "complete"/"error", don't hang forever.
            TTS_STREAM_TIMEOUT = 30.0
            async def _stream_tts():
                async for raw in tts_ws:
                    await websocket.send_text(raw)
                    try:
                        if jsonlib.loads(raw).get("type") in ("complete", "error"):
                            return
                    except Exception:
                        pass
            try:
                await asyncio.wait_for(_stream_tts(), timeout=TTS_STREAM_TIMEOUT)
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "error", "stage": "tts",
                    "message": "Voice synthesis timed out — translation is shown above.",
                })
                return
        finally:
            try:
                await tts_ws.close()
            except Exception:
                pass

        await websocket.send_json({"type": "turn_complete"})


    # ── Main loop: handle turns until browser disconnects ────────────────────
    try:
        while True:
            # Wait for the first PCM frame of a new recording turn.
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                return

            if "bytes" not in message or not message["bytes"]:
                # Stray text frame before any recording — ignore.
                continue

            # Open a fresh Gnani STT connection only now (lazy, per turn).
            try:
                stt_ws = await open_stt_ws()
            except Exception as e:
                await websocket.send_json({
                    "type": "error", "stage": "stt",
                    "message": f"Could not connect to Gnani STT: {e}",
                })
                continue  # browser stays connected; user can retry

            # Capture the first frame's bytes NOW so the nested coroutine
            # doesn't close over the loop variable `message` (which will
            # have changed by the time the coroutine actually runs).
            first_frame: bytes = message["bytes"]

            transcript_text: str | None = None
            detected_lang = "en-IN"

            async def feed_audio_to_stt(frame: bytes = first_frame):
                """Forward PCM frames → Gnani until commit arrives."""
                await stt_ws.send(frame)
                while True:
                    try:
                        msg = await websocket.receive()
                    except WebSocketDisconnect:
                        return
                    if "bytes" in msg and msg["bytes"]:
                        await stt_ws.send(msg["bytes"])
                    elif "text" in msg and msg["text"]:
                        await stt_ws.send(msg["text"])
                        try:
                            if jsonlib.loads(msg["text"]).get("type") == "commit":
                                return
                        except Exception:
                            pass

            async def collect_transcript():
                nonlocal transcript_text, detected_lang
                async for raw in stt_ws:
                    try:
                        gnani_msg = jsonlib.loads(raw)
                    except Exception:
                        continue
                    kind = gnani_msg.get("type")
                    if kind == "transcript":
                        # Fix #13: Gnani may use "text" or "transcript" as the
                        # key — check both so we never silently drop the result.
                        transcript_text = (
                            gnani_msg.get("text")
                            or gnani_msg.get("transcript")
                            or ""
                        )
                        detected_lang = gnani_msg.get("detected_language_code") or "en-IN"
                        return
                    elif kind == "error":
                        await websocket.send_json({
                            "type": "error", "stage": "stt",
                            "message": gnani_msg.get("message"),
                        })
                        transcript_text = None
                        return
                    # Fix #4: ignore other known intermediate Gnani message
                    # types (connected, processing, silence) and keep looping.
                    # Any unrecognised type is also ignored — we never hang.

            try:
                await asyncio.gather(feed_audio_to_stt(), collect_transcript())
            finally:
                try:
                    await stt_ws.close()
                except Exception:
                    pass

            if transcript_text is None:
                continue  # STT error already sent to browser
            if not transcript_text.strip():
                await websocket.send_json({"type": "no_speech"})
                continue

            await handle_one_turn(transcript_text, detected_lang)

    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# Run with: uvicorn main:app --reload --port 8000