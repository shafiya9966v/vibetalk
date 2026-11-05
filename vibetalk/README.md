# VibeTalk — Step 1: Foundation

This is the first milestone from the build plan: a React + TypeScript + Vite
frontend and a Python + FastAPI backend, wired together with a single
trivial round-trip call. No Prisma, no Timbre, no app logic yet — the only
goal here is to prove frontend ↔ backend works before anything else is built
on top of it.

## What's in here

```
vibetalk/
├── backend/
│   ├── main.py            FastAPI app with GET /api/ping
│   ├── requirements.txt
│   └── .env.example       Placeholders for Prisma/Timbre keys (Step 2)
└── frontend/
    ├── src/
    │   ├── App.tsx         Calls /api/ping on load, shows connection status
    │   └── main.tsx
    ├── index.html
    └── package.json
```

## Run the backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Visit http://localhost:8000/api/ping — you should see JSON back.

## Run the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Visit http://localhost:5173 — you should see a card that says
**"Connected to backend"** with the message and timestamp from the API.
If it says "Could not connect," make sure the backend is running first.

## Step 2: Test Prisma and Timbre directly

Before wiring anything into the app, confirm your API key and the two
endpoints actually work.

1. Get your API key from the Gnani API dashboard (Add API Key, enable both
   Speech to Text and Text to Speech scopes).
2. In `backend/`, copy `.env.example` to `.env` and paste your key into
   `GNANI_API_KEY`. Leave `GNANI_BASE_URL` as `https://api.vachana.ai`.
3. With your backend venv active (`pip install -r requirements.txt` if you
   haven't), run:

   ```bash
   # STT — pass WAV/MP3/M4A or MP4 directly (MP4 is auto-converted on the fly)
   python test_prisma_stt.py english.mp4 en-IN
   python test_prisma_stt.py hindi.mp4 hi-IN
   python test_prisma_stt.py telugu.mp4 te-IN

   # Optional: batch convert all MP4 recordings to WAV
   python convert_audio.py --all

   # TTS — writes output.wav you can play back
   python test_timbre_tts.py "Namaste, idi oka test." Nalini hi-IN
   ```

Both scripts now use the **confirmed real endpoints** from Gnani's docs:

- **STT:** `POST https://api.vachana.ai/stt/v3`, multipart form data, field
  `audio_file` + `language_code` (e.g. `hi-IN`, `te-IN`, `en-IN`) +
  `format=transcribe` for cleaned-up numbers/currency/dates.
- **TTS:** `POST https://api.vachana.ai/api/v1/tts/inference`, JSON body
  with `text`, `voice`, `model: "timbre-v2.5"`, `language` (use `hi-en` for
  Hinglish, or `auto` for mixed content), and `audio_config` for WAV output.
  Returns raw audio bytes directly on success.

Both scripts print the documented error shape
(`{"success": false, "error": {"type", "message"}}`) on failure, so a 403
will clearly say to check your API key/credits, a 400 will say what was
invalid, etc.

**Picking a voice:** `Nalini` (Hindi) is the default in the script. Check
the Timbre v2.5 voice catalog for a Telugu-preferred voice if you want
Telugu output specifically — passing `language=te-IN` alone doesn't change
which voice speaks, only how the text is normalized.

Once both scripts return a real transcript and a playable `output.wav`,
you've validated the riskiest part of the project — move on to Step 3
(wiring the real end-to-end loop through the FastAPI backend).
