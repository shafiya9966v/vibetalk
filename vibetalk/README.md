# VibeTalk

A real-time, two-person voice translator for Indian languages. Each person
speaks in their own language — VibeTalk transcribes, translates, and plays
the reply back in real time, in the language they chose to hear.

Built for the **Great Indian AI Internship Challenge** (Gnani AI).
See [`PITCH.md`](./PITCH.md) for the one-page solution pitch.

## What it actually does

- Two people, two sides of the screen, split down the middle
- Each person picks what language they want **to hear replies in** — the
  language they're speaking is detected automatically, no manual selection
- Tap to talk, speak, tap to stop — the pipeline runs live:
  **speech → transcript → translation → spoken reply**, streamed back
  progressively rather than making you wait for the whole thing to finish
- Handles natural code-mixed Indian speech (Hindi-English, Telugu-English,
  etc.) — not just one clean language at a time

## What it's built on

- **Speech-to-text:** Gnani **Prisma v3.0**, using `indic-auto` language
  detection and `transcribe` output mode, over Prisma's Realtime WebSocket
  API (`wss://api.vachana.ai/stt/v3/stream`)
- **Text-to-speech:** Gnani **Timbre v2.5**, over its Realtime WebSocket
  API (`wss://api.vachana.ai/api/v1/tts`), streamed back in chunks
- **Translation:** MyMemory (free tier), since Gnani's platform doesn't
  currently expose a translation endpoint — chosen specifically because it
  needs no GPU and runs reliably without an LLM provider
- **Backend:** Python + FastAPI, orchestrating both Gnani connections and
  the translation step over one combined WebSocket (`/ws/bridge`)
- **Frontend:** React + TypeScript + Vite, capturing raw 16kHz PCM audio
  directly from the microphone and streaming it live (not record-then-upload)

## Project structure

```
vibetalk/
├── backend/
│   ├── main.py              FastAPI app — REST fallback + the real-time
│   │                        /ws/bridge pipeline (STT → translate → TTS)
│   ├── gnani_client.py      Shared Prisma/Timbre REST wrapper (fallback path)
│   ├── translator.py        MyMemory-based translation
│   ├── language_utils.py    Supported language codes + voice selection
│   ├── requirements.txt
│   └── .env.example         Copy to .env and add your real Gnani API key
└── frontend/
    ├── src/
    │   ├── BridgeMode.tsx    The real two-person app
    │   ├── pcmCapture.ts     Raw PCM mic capture (16kHz, exact frame sizing)
    │   ├── bridge.css        Styling
    │   ├── App.tsx
    │   └── main.tsx
    ├── index.html
    └── package.json
```

## Running it locally

**1. Backend**

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then paste your real Gnani API key into it
uvicorn main:app --reload --port 8000
```

Get your API key from the Gnani API dashboard (enable both Speech-to-Text
and Text-to-Speech scopes when creating it).

**Note on ffmpeg:** the main live app (the real-time two-person pipeline)
does **not** need ffmpeg — the browser captures and streams raw audio
directly. ffmpeg is only required if you use the REST fallback endpoint
(`/api/bridge`) or the standalone test scripts with non-WAV audio files
(e.g. `.mp4`), since those convert the file format before sending it to
Gnani. Install it from [ffmpeg.org](https://ffmpeg.org/download.html) if
you plan to use either of those.

**2. Frontend** (in a second terminal)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Click **Start conversation** (this requests
microphone permission), then tap either side's mic button to talk.

## Known limitations

- Both people currently speak into the **same device** (hand it back and
  forth, or lean into one mic) — this is a single-browser-tab demo, not a
  two-phones-over-the-network product
- Realtime TTS connections are opened fresh per turn rather than reused,
  after testing showed Gnani's (very recently launched) TTS Realtime
  endpoint returning corrupted responses under rapid connection reuse —
  simple and reliable was chosen over a faster but flaky approach
- Translation quality depends on MyMemory's free tier, which handles clean
  sentences well but can struggle with casual, heavily code-mixed phrasing
- Tested primarily on desktop Chrome/Edge; mobile browser support for the
  raw PCM capture approach hasn't been verified

## Testing

See `STEP2_TESTING_LOG.md` for a walkthrough of specific edge cases this
app has been tested against (same-language turns, concurrent speaking,
mic permission denial, translation quota limits, and more) and how to
verify each one still works.
