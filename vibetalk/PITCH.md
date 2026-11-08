# VibeTalk
### A real-time, two-person voice translator built for how India actually speaks

## The problem

Existing conversation-translator tools (Google Translate's conversation
mode, Samsung/Apple live translation) assume each person speaks **one
clean language at a time**. Real Indian conversations don't work that
way — people switch between Hindi, Telugu, English and more **within the
same sentence**. A tool that forces a single-language choice per turn
breaks the moment someone naturally code-switches, which is most of the
time.

## What VibeTalk does

Two people, two sides of one screen. Each person picks only the one
thing that's genuinely theirs to choose — **what language they want to
hear replies in**. The language they're *speaking* is detected
automatically, live, per utterance. Tap to talk, speak naturally
(mixing languages if that's how you talk), tap to stop — VibeTalk
transcribes, translates, and speaks the reply back, streamed
progressively rather than making either person wait for one big
round-trip.

## Why this is hard, not just "another translator wrapper"

- **Real-time, bidirectional audio**, not record-then-upload: raw 16kHz
  PCM is captured and streamed live to the backend, which relays it to
  Gnani's STT over a persistent WebSocket session
- **Automatic language detection**, not a dropdown: built on Gnani
  **Prisma v3.0**'s `indic-auto` detection — launched days before this
  build — with no fallback multi-call hack
- **Streamed speech synthesis**: Gnani **Timbre v2.5**'s Realtime
  WebSocket API streams audio back in chunks, so playback can start
  before the full response finishes generating
- **Built, broke, and fixed real production issues** along the way:
  WebSocket disconnect races, VAD segment-boundary transcript loss on
  mid-sentence pauses, and connection-reuse corruption on a newly
  launched beta endpoint — documented and tested, not hand-waved

## Tech stack

| Layer | Technology |
|---|---|
| Speech-to-text | Gnani Prisma v3.0 (Realtime WebSocket, `indic-auto`) |
| Text-to-speech | Gnani Timbre v2.5 (Realtime WebSocket, streamed) |
| Translation | MyMemory (lightweight, GPU-free) |
| Backend | Python, FastAPI, WebSockets |
| Frontend | React, TypeScript, Vite, raw Web Audio API capture |

## Honest limitations (stated up front, not discovered by a judge)

- Both people currently speak into one shared device — a two-phones,
  network-connected version is a natural next step, not yet built
- Translation quality depends on a free-tier provider and can be
  imperfect on heavily casual, idiomatic phrasing
- Tested primarily on desktop Chrome/Edge

## Why it fits the brief

VibeTalk is a genuine, working demonstration of Gnani's own stated
differentiator — code-mixed Indian speech handled natively — built as a
real, demoable product rather than a single-language toy example.

---
*Built solo for the Great Indian AI Internship Challenge.*