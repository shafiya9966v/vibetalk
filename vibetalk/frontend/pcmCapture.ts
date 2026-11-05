/**
 * pcmCapture.ts — put in vibetalk/frontend/src/pcmCapture.ts
 *
 * Raw PCM mic capture, extracted from the validated standalone test page
 * (test_realtime_full.html) into a reusable module for the real app.
 * Downsamples the mic's native rate to 16kHz and frames audio into exact
 * 1024-byte chunks, as Gnani's STT Realtime API requires.
 */

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 512; // 512 samples * 2 bytes = 1024 bytes, per the API spec

function downsample(buffer: Float32Array, inputRate: number, outputRate: number): Float32Array {
    if (outputRate === inputRate) return buffer;
    const ratio = inputRate / outputRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    let offsetResult = 0, offsetBuffer = 0;
    while (offsetResult < newLength) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
        let accum = 0, count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
            accum += buffer[i];
            count++;
        }
        result[offsetResult] = count > 0 ? accum / count : 0;
        offsetResult++;
        offsetBuffer = nextOffsetBuffer;
    }
    return result;
}

function floatTo16BitPCM(float32Array: Float32Array): Int16Array {
    const int16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
        const s = Math.max(-1, Math.min(1, float32Array[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return int16;
}

export interface PcmCaptureHandle {
    stop: () => void;
}

/**
 * Starts capturing mic audio and calls onFrame with each exact 1024-byte
 * frame as it's ready, in real time. Call .stop() to end capture and
 * release the microphone.
 */
export async function startPcmCapture(onFrame: (frame: ArrayBuffer) => void): Promise<PcmCaptureHandle> {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
    const audioContext = new AudioContextClass();
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);

    let sampleBuffer: number[] = [];

    processor.onaudioprocess = (e: AudioProcessingEvent) => {
        const inputData = e.inputBuffer.getChannelData(0);
        const downsampled = downsample(inputData, audioContext.sampleRate, TARGET_SAMPLE_RATE);
        const pcm16 = floatTo16BitPCM(downsampled);
        for (let i = 0; i < pcm16.length; i++) sampleBuffer.push(pcm16[i]);
        while (sampleBuffer.length >= FRAME_SAMPLES) {
            const frameSamples = sampleBuffer.splice(0, FRAME_SAMPLES);
            const frame = new Int16Array(frameSamples);
            onFrame(frame.buffer);
        }
    };

    source.connect(processor);
    const mute = audioContext.createGain();
    mute.gain.value = 0;
    processor.connect(mute);
    mute.connect(audioContext.destination);

    return {
        stop: () => {
            // Flush any leftover partial frame, padded with silence.
            if (sampleBuffer.length > 0) {
                const padded = new Int16Array(FRAME_SAMPLES);
                padded.set(sampleBuffer);
                onFrame(padded.buffer);
                sampleBuffer = [];
            }
            processor.disconnect();
            source.disconnect();
            stream.getTracks().forEach((t) => t.stop());
            audioContext.close();
        },
    };
}