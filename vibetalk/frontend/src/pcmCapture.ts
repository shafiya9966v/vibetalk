/**
 * pcmCapture.ts — captures mic audio as raw 16-bit PCM frames and calls
 * `onFrame(ArrayBuffer)` for each chunk so the caller can stream them
 * over a WebSocket.
 *
 * Why raw PCM instead of MediaRecorder blobs?
 * - WebSocket-based streaming STT APIs (like Gnani's) typically expect a
 *   continuous stream of raw PCM bytes, not a self-contained container
 *   format like webm.
 * - Raw PCM (16-bit, 16 kHz, mono) is what most STT engines want.
 *
 * Usage:
 *   const handle = await startPcmCapture((frame) => ws.send(frame));
 *   // ... later ...
 *   handle.stop();
 *
 * Edge cases handled:
 * - Permission denied → throws with a clear message
 * - AudioWorklet not supported → falls back to ScriptProcessor
 * - stop() called before start() completes → safe no-op
 */

export interface PcmCaptureHandle {
  stop: () => void;
}

const SAMPLE_RATE = 16000; // Hz — what most STT engines expect
const FRAME_SIZE = 4096;   // samples per chunk (~256 ms at 16 kHz)

/** Convert a Float32 PCM frame [-1, 1] to Int16 PCM bytes. */
function float32ToInt16(input: Float32Array): ArrayBuffer {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return output.buffer;
}

/**
 * Start capturing PCM from the microphone.
 *
 * @param onFrame  Called with each raw Int16 PCM ArrayBuffer chunk.
 * @returns        A handle with a `stop()` method.
 */
export async function startPcmCapture(
  onFrame: (frame: ArrayBuffer) => void
): Promise<PcmCaptureHandle> {
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
  } catch (err) {
    throw new Error(
      err instanceof Error
        ? `Microphone unavailable: ${err.message}`
        : "Microphone permission was denied or unavailable."
    );
  }

  const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  const source = ctx.createMediaStreamSource(stream);

  let stopped = false;

  // Try AudioWorklet first (modern, runs off main thread).
  // Fall back to the deprecated ScriptProcessor if not available.
  if (ctx.audioWorklet) {
    // Inline worklet as a Blob URL so we don't need an extra file.
    const workletCode = `
      class PcmProcessor extends AudioWorkletNode {
        process(inputs) {
          const input = inputs[0];
          if (input && input[0]) {
            this.port.postMessage(input[0].slice());
          }
          return true;
        }
      }
      registerProcessor('pcm-processor', class extends AudioWorkletProcessor {
        process(inputs) {
          const ch = inputs[0]?.[0];
          if (ch) this.port.postMessage(ch.slice());
          return true;
        }
      });
    `;
    const blob = new Blob([workletCode], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);

    try {
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);

      const worklet = new AudioWorkletNode(ctx, "pcm-processor");
      worklet.port.onmessage = (e: MessageEvent<Float32Array>) => {
        if (!stopped) onFrame(float32ToInt16(e.data));
      };
      source.connect(worklet);
      worklet.connect(ctx.destination); // needed to keep graph alive in some browsers

      return {
        stop() {
          stopped = true;
          worklet.disconnect();
          source.disconnect();
          stream.getTracks().forEach((t) => t.stop());
          ctx.close();
        },
      };
    } catch {
      // AudioWorklet failed (e.g. cross-origin restriction on blob URL) —
      // fall through to ScriptProcessor below.
      URL.revokeObjectURL(url);
    }
  }

  // ScriptProcessor fallback (deprecated but universally supported).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const processor = (ctx as any).createScriptProcessor(FRAME_SIZE, 1, 1);
  processor.onaudioprocess = (e: AudioProcessingEvent) => {
    if (!stopped) {
      const samples = e.inputBuffer.getChannelData(0);
      onFrame(float32ToInt16(samples));
    }
  };
  source.connect(processor);
  processor.connect(ctx.destination);

  return {
    stop() {
      stopped = true;
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
    },
  };
}

