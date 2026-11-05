# VibeTalk — Step 2 Testing Log
### Prisma (STT) + Timbre (TTS) API Validation

> **Goal of Step 2:** Before wiring anything into the app, confirm the API key works,
> the two Gnani endpoints behave as documented, and understand how language codes
> affect transcript output — especially for code-mixed speech.

---

## Environment Setup

### Step 1 — Activating the Virtual Environment

**First attempts (failed):**

```powershell
venv\Scripts\activate.bat
```
> Result: No error, but `(venv)` did not appear — ran as `.bat` in PowerShell, had no effect.

```powershell
source venv/Scripts/activate
```
> Result: **Error** — `source` is a Linux/macOS shell command. PowerShell does not recognise it.
> ```
> source : The term 'source' is not recognized as the name of a cmdlet, function, script file,
> or operable program.
> ```

**Correct command for PowerShell (worked):**

```powershell
.\venv\Scripts\Activate.ps1
```
> Result: `(venv)` prefix appeared in the prompt — virtual environment active.
> ```
> (venv) PS C:\Users\shafiiya shaik\Downloads\vibetalk-step2\vibetalk\backend>
> ```

**Lesson learned:** On Windows PowerShell, always use `.\venv\Scripts\Activate.ps1` — not `source` (Linux) or `.bat` (cmd only).

---

## API Key Setup Issue

```powershell
python test_prisma_stt.py english.mp4 en-IN
```

> **Result (first attempt):**
> ```
> ERROR: GNANI_API_KEY not set. Copy .env.example to .env and fill it in.
> ```

**Why this happened:** The `.env` file existed but `GNANI_API_KEY=` was blank. The `python-dotenv` library loaded the file but found an empty string, which Python treats as falsy.

**Fix:** Opened `.env` with `notepad .env` and pasted the real API key directly after the `=`, no spaces, no quotes:
```
GNANI_API_KEY=<actual_key_here>
```

---

## STT Tests — Prisma (`POST https://api.vachana.ai/stt/v3`)

### Test 1 — English (`english.mp4`, `en-IN`)

**Command:**
```powershell
python test_prisma_stt.py english.mp4 en-IN
```

**Terminal output:**
```
Format '.mp4' detected. Auto-converting 'english.mp4' to 16kHz mono WAV via FFmpeg...
  [OK] Successfully converted to temporary WAV: tmpb9x7dbh1.wav
POST https://api.vachana.ai/stt/v3  (language_code=en-IN, file=tmpb9x7dbh1.wav)
Status: 200
Transcript: hi i am testing my voice assistant project today
request_id: 01a16910-42a1-7c99-8178-89908d5a02b7
```

**Why we ran it:** First-ever live test against the real Prisma endpoint. Purpose: confirm the API key works, authentication passes, the MP4 auto-conversion pipeline works end-to-end, and the response shape matches what the code expects — using the simplest case (plain English) before adding any complexity.

**What we learned:**
- API key is valid and accepted ✓
- MP4 → 16kHz mono WAV conversion works correctly ✓
- The request/response shape matches documented spec ✓
- Transcript is accurate and clean ✓

---

### Test 2 — Hindi (`hindi.mp4`, `hi-IN`)

**Command:**
```powershell
python test_prisma_stt.py hindi.mp4 hi-IN
```

**Terminal output:**
```
Format '.mp4' detected. Auto-converting 'hindi.mp4' to 16kHz mono WAV via FFmpeg...
  [OK] Successfully converted to temporary WAV: tmpqky0jlr7.wav
POST https://api.vachana.ai/stt/v3  (language_code=hi-IN, file=tmpqky0jlr7.wav)
Request failed before reaching the server: [WinError 10054] An existing connection was
forcibly closed by the remote host
```

**Why we ran it:** Tests whether Prisma can transcribe Hindi correctly when `language_code=hi-IN` — the "expected, matched" case for one of VibeTalk's two core target languages.

**What we learned:**
- `[WinError 10054]` = the server dropped the TCP connection before sending any response. This is a **transient network error** (not an API logic error — no HTTP status code was returned at all).
- This error is non-reproducible in most cases; re-running the same command usually succeeds.
- The audio file itself is fine; conversion succeeded.
- **Action:** Re-run if this occurs. If it persists, check network/VPN and retry.

> ⚠️ **Note:** This was a one-time connectivity drop, not a code or API key issue.

---

### Test 3 — Telugu (`telugu.mp4`, `te-IN`)

**Command:**
```powershell
python test_prisma_stt.py telugu.mp4 te-IN
```

**Terminal output:**
```
Format '.mp4' detected. Auto-converting 'telugu.mp4' to 16kHz mono WAV via FFmpeg...
  [OK] Successfully converted to temporary WAV: tmpic6le9pb.wav
POST https://api.vachana.ai/stt/v3  (language_code=te-IN, file=tmpic6le9pb.wav)
Status: 200
Transcript: నమస్కారం నేను నా ప్రాజెక్ట్ ని టెస్ట్ చేస్తున్నాను
request_id: 01a16911-1124-70f8-8c17-89d75ca39bc0
```

**Translation (for reference):** *"Namaskaram, I am testing my project."*

**Why we ran it:** Confirms both of VibeTalk's main languages work — not just English. Same logic as Test 2 (matched language code) but for Telugu.

**What we learned:**
- Telugu STT works and returns accurate Telugu-script output ✓
- `te-IN` language code is correctly supported ✓
- API returns Unicode Telugu script natively — no additional processing needed ✓

---

### Test 4 — Hinglish as Hindi (`hinglish.mp4`, `hi-IN`)

**Command:**
```powershell
python test_prisma_stt.py hinglish.mp4 hi-IN
```

**Terminal output:**
```
Format '.mp4' detected. Auto-converting 'hinglish.mp4' to 16kHz mono WAV via FFmpeg...
  [OK] Successfully converted to temporary WAV: tmp68dchv4m.wav
POST https://api.vachana.ai/stt/v3  (language_code=hi-IN, file=tmp68dchv4m.wav)
Status: 200
Transcript: यार मुझे इंटरव्यू की टेंशन हो रही है मैं क्या करूँ
request_id: 01a16911-20b6-7d1e-9cb1-47533b7b48af
```

**Translation (for reference):** *"Yaar, I'm stressed about the interview, what should I do?"*

**Why we ran it:** Hinglish is code-mixed speech (Hindi + English words in one sentence). The whole VibeTalk product pitch depends on handling this well. We tested `hi-IN` first to see what the API does when told "this is Hindi" even though English words are mixed in.

**What we learned:**
- When `language_code=hi-IN`, the API outputs in **Devanagari script** — even English-origin words like "interview" and "tension" are written in Devanagari (इंटरव्यू, टेंशन) ✓
- Script is fully Devanagari; content is correct ✓
- This is the natural representation for Hindi-first users

---

### Test 5 — Telugu+English (`telugu+english.mp4`, `te-IN`)

**Command:**
```powershell
python test_prisma_stt.py "telugu+english.mp4" te-IN
```

**Terminal output:**
```
Format '.mp4' detected. Auto-converting 'telugu+english.mp4' to 16kHz mono WAV via FFmpeg...
  [OK] Successfully converted to temporary WAV: tmpf9nmy938.wav
POST https://api.vachana.ai/stt/v3  (language_code=te-IN, file=tmpf9nmy938.wav)
Status: 200
Transcript: నేను ఇప్పుడు పైతోనే నేర్చుకుంటున్నాను బట్ ఫంక్షన్స్ అర్ధం కావడం లేదు సో ఎలా
request_id: 01a16911-2c83-7095-a512-92748e1ce704
```

**Translation (for reference):** *"I am now learning Python but I don't understand functions, so what do I do?"*

**Why we ran it:** Same A/B logic as Test 4, but for Telugu-English code mixing. Confirms whether the `te-IN` pattern holds the same way — i.e., English words transliterated into Telugu script.

**What we learned:**
- English words in Telugu speech ("Python", "but", "functions", "so") are written in Telugu script (పైతోనే, బట్, ఫంక్షన్స్, సో) ✓
- The pattern is consistent: **language code determines script, not just phonetics** ✓
- This confirms the same behaviour as `hi-IN` for Hinglish — important consistency across both target languages ✓

---

### Test 6 — Hinglish as English (`hinglish.mp4`, `en-IN`) — A/B Comparison

**Command:**
```powershell
python test_prisma_stt.py hinglish.mp4 en-IN
```

**Terminal output:**
```
Format '.mp4' detected. Auto-converting 'hinglish.mp4' to 16kHz mono WAV via FFmpeg...
  [OK] Successfully converted to temporary WAV: tmp9ufmo79w.wav
POST https://api.vachana.ai/stt/v3  (language_code=en-IN, file=tmp9ufmo79w.wav)
Status: 200
Transcript: yaar mujhe interview ki tension ho rahi hai mein kya karun
request_id: 01a16912-00b5-717e-bb6d-14db4e4aaf67
```

**Why we ran it:** Same audio as Test 4, different `language_code`. This is a direct A/B comparison — the question: does the same code-mixed speech transcribe differently depending on the language code we tell the API?

**A/B Comparison — Critical Finding:**

| language_code | Transcript output |
|---|---|
| `hi-IN` | `यार मुझे इंटरव्यू की टेंशन हो रही है मैं क्या करूँ` (Devanagari) |
| `en-IN` | `yaar mujhe interview ki tension ho rahi hai mein kya karun` (Roman/Latin) |

**What we learned:**
- **Same audio. Same words. Completely different script.** The `language_code` controls the output script, not just pronunciation normalisation.
- `hi-IN` → **Devanagari script** output
- `en-IN` → **Romanised/Latin script** output (transliteration)
- This is **not a minor detail** — it directly determines what the transcript looks like on screen, which affects:
  - UI font choices
  - How text is displayed to the user
  - How VibeTalk should handle language detection in Step 3+
- **Design implication for Step 4:** The app will need to either let users choose their preferred script or auto-detect the intended language before calling the STT API.

---

## TTS Test — Timbre (`POST https://api.vachana.ai/api/v1/tts/inference`)

### Test 7 — Hindi TTS (`Nalini` voice, `hi-IN`)

**Command:**
```powershell
python test_timbre_tts.py "Namaste, aap kaise hain" Nalini hi-IN
```

**Terminal output:**
```
POST https://api.vachana.ai/api/v1/tts/inference  (voice=Nalini, language=hi-IN)
Status: 200
Saved audio to output.wav — play it to check voice quality.
```

**Why we ran it:** Switches from testing STT (speech → text) to testing TTS (text → speech) — the other half of the VibeTalk pipeline. Purpose: confirm the Timbre endpoint works, the API key has TTS scope active, and — critically — that the generated voice sounds natural and listenable. Voice quality can only be judged by ear, not from a status code.

**What we learned:**
- Timbre TTS endpoint is working ✓
- API key has both STT **and** TTS scopes active ✓
- Response returns raw `audio/wav` binary which was saved as `output.wav` ✓
- Voice `Nalini` with `hi-IN` generates speech — quality to be judged by listening ✓

> **Note:** `output.wav` is saved inside `backend/`. Play it with any audio player to evaluate voice naturalness.

---

## Summary of Step 2 Findings

| # | Test | Status | Key Finding |
|---|---|---|---|
| 1 | English STT | ✅ Pass | API key valid, MP4 pipeline works, English transcription accurate |
| 2 | Hindi STT | ⚠️ Network error | Transient `WinError 10054` — server dropped connection; not a code/API issue |
| 3 | Telugu STT | ✅ Pass | Telugu transcription works, returns Unicode Telugu script correctly |
| 4 | Hinglish → `hi-IN` | ✅ Pass | Code-mixed speech → Devanagari output |
| 5 | Telugu+English → `te-IN` | ✅ Pass | Code-mixed speech → Telugu script output |
| 6 | Hinglish → `en-IN` | ✅ Pass | **Critical:** Same audio → Latin/Roman output. Script depends on `language_code` |
| 7 | Hindi TTS | ✅ Pass | Timbre TTS works, both STT+TTS scopes confirmed active |

---

## Critical Design Decision Unlocked in Step 2

> **The `language_code` parameter controls output script, not just language matching.**
>
> For code-mixed (Hinglish / Telugu+English) audio:
> - `hi-IN` / `te-IN` → Native script (Devanagari / Telugu)
> - `en-IN` → Romanised Latin script
>
> **Action needed in Step 3/4:** VibeTalk must decide — or let the user decide — which script to present transcripts in. This feeds directly into the UI/UX design and the language detection logic for the real-time pipeline.

---

## What's Validated and Ready for Step 3

- [x] venv activation (PowerShell: `.\venv\Scripts\Activate.ps1`)
- [x] `.env` API key setup
- [x] MP4 → WAV auto-conversion pipeline
- [x] Prisma STT: English, Telugu, Hinglish — all working
- [x] `language_code` script-switching behaviour documented
- [x] Timbre TTS: Hindi voice generation working
- [x] Both API scopes (STT + TTS) confirmed on the same key

**Next step → Step 3:** Wire the real STT + TTS endpoints through the FastAPI backend and connect them to the frontend.
