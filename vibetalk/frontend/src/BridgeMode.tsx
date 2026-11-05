/**
 * BridgeMode.tsx
 *
 * Layout: both "You said" AND "Translation" appear in the SPEAKER's panel.
 * Person A speaks → A's panel shows transcript + translation stacked together.
 * Person B speaks → B's panel shows transcript + translation stacked together.
 *
 * Fixes included:
 * - Landing/intro screen with mic permission context before entering the app
 * - "New conversation" button to clear message history
 * - Double-tap guard: an in-flight lock prevents overlapping recordings
 * - Language dropdown race condition: mic is physically disabled during WebSocket
 *   reconnect that happens when target_lang changes
 * - Visible "Reconnecting…" state — idle WS close is surfaced clearly
 * - Humanized error messages — raw backend strings rewritten to plain language
 * - Same-language: skip message shown instead of raw translation
 * - Long speech (>60s) and Gnani API down: surfaced as plain-language errors
 * - TEMPORARY DEBUG: "Download audio" link next to "Play again" so the exact
 *   played WAV file can be saved and inspected if playback sounds wrong
 */

import { useEffect, useRef, useState, useCallback } from "react";
import "./bridge.css";
import { startPcmCapture, type PcmCaptureHandle } from "./pcmCapture";

// Use wss/https in production (served over HTTPS), ws/http on localhost.
const IS_SECURE = window.location.protocol === "https:";
const WS_BASE = IS_SECURE
  ? `wss://${window.location.host}`
  : "ws://localhost:8000";
const API_BASE_URL = IS_SECURE
  ? `https://${window.location.host}`
  : "http://localhost:8000";

const WS_BRIDGE_URL = `${WS_BASE}/ws/bridge`;

type SideId = "a" | "b";
// "reconnecting" is a separate state for when the WebSocket died and we're
// waiting to reconnect — it means the mic must stay physically blocked.
type RecordingState =
  | "idle"
  | "connecting"
  | "reconnecting"
  | "recording"
  | "sending"
  | "disabled";

interface Message {
  id: string;
  side: SideId;
  kind: "own" | "translation";
  text: string;
  audioChunks?: string[];
  note?: string;
  detectedLang?: string | null;
  pending?: boolean;
}

// Fix #2/#6: track which message id is the current "pending" translation so
// audio chunks and turn_complete always update the right bubble, even when
// both sides are active at the same time.
interface BridgeSideState {
  state: RecordingState;
  errorMsg: string | null;
  toggleRecording: () => Promise<void>;
  dismissError: () => void;
}

function SideLabel(side: SideId) {
  return side === "a" ? "Person A" : "Person B";
}

/** Convert raw backend error strings into plain human-readable messages. */
function humanizeError(raw: string): string {
  const s = raw.toLowerCase();
  if (s.includes("could not connect to gnani stt")) {
    return "Could not reach the speech recognition service — check your internet connection and try again.";
  }
  if (s.includes("could not connect to gnani tts") || s.includes("voice playback failed")) {
    return "Voice playback unavailable — translation text is shown above. The speech service may be temporarily down.";
  }
  if (s.includes("timed out") && s.includes("tts")) {
    return "Voice synthesis timed out — translation text is shown above.";
  }
  if (s.includes("gnani") && s.includes("tts")) {
    return "Voice synthesis failed — translation text is shown above.";
  }
  if (s.includes("gnani") && s.includes("stt")) {
    return "Speech recognition failed — the service may be temporarily unavailable.";
  }
  if (s.includes("translation failed") || s.includes("translate:")) {
    return "Translation failed — please try again in a moment.";
  }
  if (s.includes("60") || s.includes("duration") || s.includes("too long") || s.includes("max_duration")) {
    return "Recording too long — please keep each turn under 60 seconds.";
  }
  // Fix #7: MyMemory free tier daily quota exhaustion shows a clear message.
  if (s.includes("rate limit") || s.includes("429") || s.includes("quota") ||
      s.includes("mymemory") || s.includes("daily") || s.includes("limit exceeded")) {
    return "Translation limit reached for today — free quota exhausted. Try again tomorrow or switch to a paid translation provider.";
  }
  if (s.includes("notallowederror") || s.includes("permissiondeniederror") ||
      s.includes("permission denied") || s.includes("permission dismissed")) {
    return "Microphone permission was denied. Please allow microphone access in your browser settings and try again.";
  }
  if (s.includes("microphone") || s.includes("getusermedia") || s.includes("could not start")) {
    return IS_SECURE
      ? "Microphone unavailable — check that no other app is using it."
      : "Microphone unavailable. Make sure this page is on localhost or served over HTTPS.";
  }
  if (s.includes("no speech") || s.includes("silence")) {
    return "No speech was detected — try speaking a bit louder or closer to the mic.";
  }
  const prefixStripped = raw.replace(/^(translate|stt|tts|error):\s*/i, "");
  return prefixStripped.length > 120
    ? prefixStripped.slice(0, 120) + "…"
    : prefixStripped;
}

function BlobAvatar({ state }: { state: RecordingState }) {
  return (
    <div className="blob" data-state={state}>
      <div className="blob-face">
        <div className="blob-eyes">
          <div className="blob-eye" />
          <div className="blob-eye" />
        </div>
        <div className="blob-smile" />
      </div>
    </div>
  );
}

/**
 * Merge base64-encoded audio chunks into a single correct WAV file.
 *
 * Gnani streams one WAV split across chunks:
 *   chunk 0 = WAV header (44 bytes) + first PCM block
 *   chunk 1+ = raw PCM continuation bytes, NO headers
 *
 * The WAV header written by Gnani only knows the size of chunk 0's PCM.
 * After concatenating all chunks we must patch two fields in the header
 * so the browser's decoder sees the correct total length and doesn't
 * truncate playback to just the first chunk:
 *   bytes  4–7:  RIFF chunk size  = totalBytes - 8
 *   bytes 40–43: data subchunk size = total PCM bytes
 */
function mergeAudioChunks(chunks: string[]): ArrayBuffer {
  const decoded = chunks.map((b64) => {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  });

  const totalLen = decoded.reduce((s, c) => s + c.length, 0);
  const merged = new Uint8Array(totalLen);
  let off = 0;
  for (const c of decoded) { merged.set(c, off); off += c.length; }

  if (
    totalLen >= 44 &&
    merged[0] === 0x52 && merged[1] === 0x49 &&
    merged[2] === 0x46 && merged[3] === 0x46
  ) {
    const view = new DataView(merged.buffer);
    view.setUint32(4, totalLen - 8, true);
    view.setUint32(40, totalLen - 44, true);
  }

  return merged.buffer;
}

function MessageBubble({
  message,
  audioCtx,
  onPlayingChange,
}: {
  message: Message;
  audioCtx: AudioContext | null;
  onPlayingChange: (playing: boolean) => void;
}) {
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const decodedBufRef = useRef<AudioBuffer | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioReady, setAudioReady] = useState(false);
  const hasPlayedRef = useRef(false);
  // TEMP DEBUG: downloadable URL of the exact raw bytes that were played,
  // so the real WAV file can be saved and inspected if playback sounds off.
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  const playBuffer = useCallback((buf: AudioBuffer) => {
    if (!audioCtx) return;
    try { sourceRef.current?.stop(); } catch { /* already stopped */ }

    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(audioCtx.destination);
    src.onended = () => { setIsPlaying(false); onPlayingChange(false); };
    sourceRef.current = src;
    src.start(0);
    setIsPlaying(true);
    onPlayingChange(true);
  }, [audioCtx, onPlayingChange]);

  useEffect(() => {
    if (message.kind !== "translation") return;
    if (message.pending) return;
    const chunks = message.audioChunks;
    if (!chunks || chunks.length === 0) return;
    if (!audioCtx) return;

    const merged = mergeAudioChunks(chunks);

    // TEMP DEBUG: save a downloadable copy of the exact raw bytes.
    const blobUrl = URL.createObjectURL(new globalThis.Blob([merged], { type: "audio/wav" }));
    setDownloadUrl(blobUrl);

    audioCtx.decodeAudioData(merged.slice(0)).then((buf) => {
      decodedBufRef.current = buf;
      setAudioReady(true);
      if (!hasPlayedRef.current) {
        hasPlayedRef.current = true;
        playBuffer(buf);
      }
    }).catch((err) => {
      console.warn("[audio] decodeAudioData failed:", err);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [message.pending, audioCtx]);

  useEffect(() => {
    return () => {
      try { sourceRef.current?.stop(); } catch { /* already stopped */ }
      onPlayingChange(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handlePlayPause = () => {
    if (!audioCtx || !decodedBufRef.current) return;
    if (isPlaying) {
      try { sourceRef.current?.stop(); } catch { /* already stopped */ }
      setIsPlaying(false);
      onPlayingChange(false);
    } else {
      playBuffer(decodedBufRef.current);
    }
  };

  if (message.kind === "own") {
    return (
      <div className="bubble bubble--own">
        <div className="bubble-meta">
          🎙 <strong>You said</strong>
          {message.detectedLang && (
            <span className="bubble-lang"> · {message.detectedLang}</span>
          )}
        </div>
        {message.note ? (
          <em className="bubble-note">{message.note}</em>
        ) : (
          <div className="bubble-text">{message.text}</div>
        )}
      </div>
    );
  }

  return (
    <div className="bubble bubble--translation">
      <div className="bubble-meta">
        🌐 <strong>Translation</strong>
        {message.pending && (
          <span className="bubble-pending"> · translating…</span>
        )}
      </div>
      {message.note ? (
        <em className="bubble-note">{message.note}</em>
      ) : (
        <>
          <div className="bubble-text">{message.text}</div>
          {audioReady && (
            <button className="play-btn" onClick={handlePlayPause}>
              {isPlaying ? "⏸ Pause" : "▶ Play again"}
            </button>
          )}
          {audioReady && downloadUrl && (
            <a
              href={downloadUrl}
              download="vibetalk-debug-audio.wav"
              className="play-btn"
              style={{ marginLeft: 6 }}
            >
              ⬇ Download audio
            </a>
          )}
        </>
      )}
    </div>
  );
}

function SidePanel({
  side,
  languages,
  language,
  setLanguage,
  messages,
  state,
  errorMsg,
  onToggleRecording,
  onDismissError,
  anyAudioPlaying,
  onPlayingChange,
  onClearHistory,
  audioCtx,
}: {
  side: SideId;
  languages: Record<string, string>;
  language: string;
  setLanguage: (code: string) => void;
  messages: Message[];
  state: RecordingState;
  errorMsg: string | null;
  onToggleRecording: () => void;
  onDismissError: () => void;
  anyAudioPlaying: boolean;
  onPlayingChange: (playing: boolean) => void;
  onClearHistory: () => void;
  audioCtx: AudioContext | null;
}) {
  const micBlocked = anyAudioPlaying && state === "idle";

  const micDisabled =
    state === "disabled" ||
    state === "sending" ||
    state === "connecting" ||
    state === "reconnecting" ||
    micBlocked;

  const hint =
    state === "disabled"
      ? "Disconnected"
      : state === "connecting"
      ? "Connecting…"
      : state === "reconnecting"
      ? "Reconnecting…"
      : state === "recording"
      ? "🔴 Listening — tap to stop"
      : state === "sending"
      ? "Translating…"
      : micBlocked
      ? "⏳ Wait for playback"
      : "Tap to talk";

  return (
    <div className={`bridge-panel bridge-panel--${side}`}>
      <div className="panel-top">
        <span className="panel-label">{SideLabel(side)}</span>
        <div className="lang-pick">
          <span className="lang-pick-hint">Translate to</span>
          <select
            className="lang-select"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={state !== "idle"}
          >
            {Object.entries(languages).map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="blob-wrap">
        <BlobAvatar state={state} />
      </div>
      <div className="state-label">{hint}</div>

      <div className="messages">
        {messages.length === 0 && (
          <p className="messages-empty">Speak to start the conversation…</p>
        )}
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            audioCtx={audioCtx}
            onPlayingChange={onPlayingChange}
          />
        ))}
      </div>

      {messages.length > 0 && (
        <button
          className="clear-btn"
          onClick={onClearHistory}
          title="Clear conversation history"
        >
          New conversation
        </button>
      )}

      {errorMsg && (
        <div className="error-banner">
          <span>{errorMsg}</span>
          <button
            className="error-dismiss"
            onClick={onDismissError}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      <div className="mic-zone">
        <button
          className="mic-btn"
          data-state={micDisabled ? "disabled" : state}
          disabled={micDisabled}
          onClick={onToggleRecording}
        >
          {state === "recording" ? "⏹ Stop" : "🎙 Tap to talk"}
        </button>
      </div>
    </div>
  );
}

function useBridgeSide(
  targetLang: string,
  side: SideId,
  addMessage: (msg: Message) => void,
  updateMessageById: (id: string, updater: (m: Message) => Message) => void
): BridgeSideState {
  const wsRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<PcmCaptureHandle | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generationRef = useRef(0);
  const toggleInFlightRef = useRef(false);
  // Fix #2/#6: track the id of the current in-flight translation bubble so
  // audio chunks and turn_complete always update the correct message, even
  // when both sides produce messages concurrently.
  const pendingMsgIdRef = useRef<string | null>(null);
  const [state, setState] = useState<RecordingState>("connecting");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    const myGeneration = ++generationRef.current;
    // Fix #9: stop any active capture before the language change disconnect
    // happens, so no stale frames get sent to the old (about-to-close) WS.
    captureRef.current?.stop();
    captureRef.current = null;

    function connect(isReconnect = false) {
      if (generationRef.current !== myGeneration) return;
      setState(isReconnect ? "reconnecting" : "connecting");

      const ws = new WebSocket(WS_BRIDGE_URL);
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        if (generationRef.current !== myGeneration) { ws.close(); return; }
        ws.send(JSON.stringify({ target_lang: targetLang }));
        setState("idle");
        setErrorMsg(null);
      };

      ws.onmessage = (event) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(event.data as string);
        } catch {
          return;
        }

        const type = msg.type as string;

        if (type === "transcript") {
          addMessage({
            id: `${Date.now()}-${side}-own`,
            side,
            kind: "own",
            text: msg.text as string,
            detectedLang: (msg.detected_language_code as string) || null,
          });
        } else if (type === "translated") {
          const translatedText = msg.text as string;
          const isSameLang = msg.same_language as boolean | undefined;
          // Fix #2: generate the id here and store it so every subsequent
          // audio/turn_complete message updates THIS specific bubble.
          const trId = `${Date.now()}-${side}-tr`;
          pendingMsgIdRef.current = trId;
          addMessage({
            id: trId,
            side,
            kind: "translation",
            text: translatedText,
            audioChunks: [],
            pending: true,
            note: isSameLang ? "Same language — no translation needed." : undefined,
          });
        } else if (type === "audio") {
          const data = msg.data as Record<string, unknown> | undefined;
          const audioB64 =
            (data?.audio as string) ||
            (data?.audio_content as string) ||
            (msg.audio as string) ||
            null;
          // Fix #2: update by id, not by last-in-array position.
          if (audioB64 && pendingMsgIdRef.current) {
            const targetId = pendingMsgIdRef.current;
            updateMessageById(targetId, (m) => ({
              ...m,
              audioChunks: [...(m.audioChunks || []), audioB64],
            }));
          }
        } else if (type === "turn_complete") {
          setState("idle");
          // Fix #6: clear pending on the specific message, not whichever
          // happens to be last in the array at this moment.
          if (pendingMsgIdRef.current) {
            const targetId = pendingMsgIdRef.current;
            pendingMsgIdRef.current = null;
            updateMessageById(targetId, (m) => ({ ...m, pending: false }));
          }
        } else if (type === "no_speech") {
          setState("idle");
          addMessage({
            id: `${Date.now()}-${side}-ns`,
            side,
            kind: "own",
            text: "",
            note: "No speech detected — try speaking closer to the mic.",
          });
        } else if (type === "error") {
          setState("idle");
          const raw = `${msg.stage ? `${msg.stage}: ` : ""}${msg.message}`;
          setErrorMsg(humanizeError(raw));
          // If a translation bubble was pending, mark it done so it doesn't
          // stay stuck showing "translating…" after an error.
          if (pendingMsgIdRef.current) {
            const targetId = pendingMsgIdRef.current;
            pendingMsgIdRef.current = null;
            updateMessageById(targetId, (m) => ({ ...m, pending: false }));
          }
        }
      };

      ws.onerror = () => {};

      ws.onclose = () => {
        if (generationRef.current !== myGeneration) return;
        captureRef.current?.stop();
        captureRef.current = null;
        setState("reconnecting");
        retryRef.current = setTimeout(() => connect(true), 2000);
      };

      wsRef.current = ws;
    }

    connect(false);

    return () => {
      generationRef.current++;
      if (retryRef.current) clearTimeout(retryRef.current);
      // Fix #9: stop capture in cleanup so no frames go to the closing WS.
      captureRef.current?.stop();
      captureRef.current = null;
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetLang]);

  const toggleRecording = useCallback(async () => {
    if (toggleInFlightRef.current) return;
    toggleInFlightRef.current = true;
    try {
      setErrorMsg(null);
      if (state === "idle") {
        setState("recording");
        try {
          captureRef.current = await startPcmCapture((frame) => {
            if (wsRef.current?.readyState === WebSocket.OPEN)
              wsRef.current.send(frame);
          });
        } catch (e) {
          // e.name is the DOMException type ("NotAllowedError"), e.message is
          // the human text ("Permission denied"). Check both so we catch every
          // browser's wording.
          const errName = e instanceof Error ? (e as DOMException).name?.toLowerCase() ?? "" : "";
          const errMsg = e instanceof Error ? e.message : "Microphone unavailable.";
          const combined = `${errName} ${errMsg}`.toLowerCase();
          setErrorMsg(humanizeError(combined));
          // Fix #5: if mic permission was denied, move to "disabled" so the
          // button stays greyed out permanently instead of looking ready again.
          const isDenied =
            combined.includes("notallowederror") ||
            combined.includes("permissiondeniederror") ||
            combined.includes("permission denied") ||
            combined.includes("permission dismissed");
          setState(isDenied ? "disabled" : "idle");
        }
      } else if (state === "recording") {
        captureRef.current?.stop();
        captureRef.current = null;
        setState("sending");
        if (wsRef.current?.readyState === WebSocket.OPEN)
          wsRef.current.send(JSON.stringify({ type: "commit" }));
      }
    } finally {
      setTimeout(() => {
        toggleInFlightRef.current = false;
      }, 300);
    }
  }, [state]);

  const dismissError = () => {
    setErrorMsg(null);
    // Fix #5: if the side was disabled due to mic denial, re-enable it when
    // the user explicitly dismisses the error — they may have granted
    // permission in browser settings by then and want to try again.
    setState((s) => s === "disabled" ? "idle" : s);
  };

  return { state, errorMsg, toggleRecording, dismissError };
}

function LandingScreen({ onStart }: { onStart: () => void }) {
  return (
    <div className="landing">
      <div className="landing-card">
        <h1 className="landing-title">VibeTalk</h1>
        <p className="landing-tagline">Two people. Two languages. One conversation.</p>
        <p className="landing-desc">
          Each person speaks in their own language — VibeTalk transcribes,
          translates, and plays the reply in real time.
        </p>
        <p className="landing-mic-note">
          🎙 You'll be asked to allow microphone access when you tap Start.
        </p>
        <button className="landing-start-btn" onClick={onStart}>
          Start conversation
        </button>
        <p className="landing-compat">
          Best on desktop Chrome or Edge. Microphone requires HTTPS on non-localhost URLs.
        </p>
      </div>
    </div>
  );
}

export default function BridgeMode() {
  const [started, setStarted] = useState(false);
  const audioCtxRef = useRef<AudioContext | null>(null);

  // Fix #12: suspend AudioContext when the tab is hidden to stop draining
  // battery / triggering browser warnings, and resume it when visible again.
  useEffect(() => {
    const handleVisibility = () => {
      const ctx = audioCtxRef.current;
      if (!ctx) return;
      if (document.hidden) {
        ctx.suspend().catch(() => {});
      } else {
        ctx.resume().catch(() => {});
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  const [languages, setLanguages] = useState<Record<string, string>>({});
  const [langA, setLangA] = useState("te-IN");
  const [langB, setLangB] = useState("hi-IN");
  const [messagesA, setMessagesA] = useState<Message[]>([]);
  const [messagesB, setMessagesB] = useState<Message[]>([]);
  const [playingCount, setPlayingCount] = useState(0);
  const anyAudioPlaying = playingCount > 0;
  const handlePlayingChange = (playing: boolean) =>
    setPlayingCount((c) => Math.max(0, c + (playing ? 1 : -1)));

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/languages`)
      .then((r) => r.json())
      .then((d) => setLanguages(d.tts_languages))
      .catch(() =>
        setLanguages({
          "hi-IN": "Hindi",
          "en-IN": "English",
          "te-IN": "Telugu",
          "ta-IN": "Tamil",
        })
      );
  }, []);

  const addTo =
    (setter: React.Dispatch<React.SetStateAction<Message[]>>) =>
    (msg: Message) =>
      setter((prev) => [...prev, msg]);

  // Fix #2/#6: update a specific message by id rather than always patching
  // the last element, so concurrent turns on both sides never collide.
  const updateById =
    (setter: React.Dispatch<React.SetStateAction<Message[]>>) =>
    (id: string, updater: (m: Message) => Message) =>
      setter((prev) => {
        const idx = prev.findIndex((m) => m.id === id);
        if (idx === -1) return prev;
        const next = [...prev];
        next[idx] = updater(next[idx]);
        return next;
      });

  const sideA = useBridgeSide(
    langB,
    "a",
    addTo(setMessagesA),
    updateById(setMessagesA)
  );
  const sideB = useBridgeSide(
    langA,
    "b",
    addTo(setMessagesB),
    updateById(setMessagesB)
  );

  if (!started) {
    return (
      <LandingScreen
        onStart={() => {
          if (!audioCtxRef.current) {
            const ctx = new AudioContext();
            ctx.resume().catch(() => {});
            audioCtxRef.current = ctx;
          }
          setStarted(true);
        }}
      />
    );
  }

  return (
    <div className="bridge-app">
      <header className="bridge-header">
        <h1 className="bridge-title">VibeTalk</h1>
        <p className="bridge-subtitle">Tap · Speak · Hear it in their language</p>
      </header>

      <div className="bridge-split">
        <SidePanel
          side="a"
          languages={languages}
          language={langB}
          setLanguage={setLangB}
          messages={messagesA}
          state={sideA.state}
          errorMsg={sideA.errorMsg}
          onToggleRecording={sideA.toggleRecording}
          onDismissError={sideA.dismissError}
          anyAudioPlaying={anyAudioPlaying}
          onPlayingChange={handlePlayingChange}
          onClearHistory={() => setMessagesA([])}
          audioCtx={audioCtxRef.current}
        />
        <div className="bridge-divider" />
        <SidePanel
          side="b"
          languages={languages}
          language={langA}
          setLanguage={setLangA}
          messages={messagesB}
          state={sideB.state}
          errorMsg={sideB.errorMsg}
          onToggleRecording={sideB.toggleRecording}
          onDismissError={sideB.dismissError}
          anyAudioPlaying={anyAudioPlaying}
          onPlayingChange={handlePlayingChange}
          onClearHistory={() => setMessagesB([])}
          audioCtx={audioCtxRef.current}
        />
      </div>
    </div>
  );
}