/**
 * Microphone capture for the composer's voice input.
 *
 * Wraps ``getUserMedia`` + ``MediaRecorder`` into a tiny state machine
 * (idle -> starting -> recording -> idle) and hands the finished clip to
 * the caller as a Blob; the caller (Composer.tsx) uploads it to
 * ``POST /transcribe`` and drops the transcript into the textarea. The
 * hook never touches the network itself.
 *
 * Browser reality this accounts for:
 * - Capture needs a secure context (https or localhost); on a plain-http
 *   dev instance reached by IP, ``navigator.mediaDevices`` is undefined.
 *   ``isVoiceInputSupported()`` is the feature check the composer uses to
 *   hide the button entirely rather than show one that cannot work.
 * - Opening the microphone is NOT instant. ``getUserMedia`` may take a
 *   moment (permission prompt, device open), and even after it resolves
 *   and ``MediaRecorder.start()`` returns, the OS audio stack can take one
 *   to three more seconds before real samples arrive -- Bluetooth headsets
 *   switch to their hands-free profile (the chime users hear), USB
 *   interfaces wake up. Everything said before that point is silence in
 *   the clip. So the hook stays in ``starting`` -- and the caller should
 *   tell the user to wait -- until an ``AnalyserNode`` on the same stream
 *   sees the first non-silent frame (a live microphone always has a noise
 *   floor; a device that has not opened yet delivers exact zeros). A
 *   hard-muted microphone would never produce signal, so ``starting``
 *   gives up after ``LIVE_SIGNAL_TIMEOUT_MS`` and flips to ``recording``
 *   anyway. The recorder itself runs from the moment it is created, so any
 *   audio that arrives earlier than our detection is still in the clip.
 * - While recording, ``inputLevel`` (0..1, quantised and throttled so it
 *   costs at most a few re-renders a second) exposes the current input
 *   loudness for a level meter: the user can see they are being heard.
 * - Chrome/Firefox/Edge record Opus-in-WebM; Safari records AAC-in-MP4.
 *   The first supported entry of ``PREFERRED_MIME_TYPES`` wins, falling
 *   back to the browser's default; the server sniffs the container anyway.
 * - Recordings auto-stop at ``MAX_RECORDING_SECONDS`` (mirrored in
 *   chat/transcription.py) so a clip can never exceed the upload cap.
 * - The microphone is opened by asking for the ``default`` device BY ID
 *   (``openMicrophone``), never with a bare ``{audio: true}``. The bare
 *   form makes Chrome on macOS wake every input device while it chooses
 *   one: with an iPhone offered through Continuity Camera that connects
 *   the phone (it chimes, and audio takes seconds to arrive) even though
 *   the built-in microphone is what ends up used. An explicit device id
 *   opens only that device. Browsers without a ``default`` alias
 *   (Firefox, Safari) reject the constraint and get the bare form.
 * - Tracks are stopped as soon as the recording ends so the browser's
 *   "microphone in use" indicator goes away immediately.
 * - ``stop()`` during ``starting`` is honoured: before the recorder exists
 *   it aborts the start (no clip, no callback), afterwards it stops the
 *   recorder normally and delivers whatever was captured.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type VoiceRecorderStatus = 'idle' | 'starting' | 'recording';

export interface VoiceRecording {
  blob: Blob;
  mimeType: string;
  filename: string;
  durationMs: number;
}

export const MAX_RECORDING_SECONDS = 120;

/** How long ``starting`` waits for the first audible frame before giving up. */
export const LIVE_SIGNAL_TIMEOUT_MS = 3000;

// RMS above which a frame counts as "the microphone is delivering audio".
// A live mic's noise floor is orders of magnitude above this; a device that
// has not opened yet (or a suspended AudioContext) delivers exact zeros.
const LIVE_SIGNAL_RMS = 1e-5;
// RMS that maps to a full level meter; ordinary speech sits around 0.05-0.3.
const FULL_SCALE_RMS = 0.25;
const LEVEL_STEPS = 10;
const LEVEL_UPDATE_INTERVAL_MS = 80;

const PREFERRED_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
];

/** Whether this browser + origin can capture microphone audio at all. */
export function isVoiceInputSupported(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false;
  if (!window.isSecureContext) return false;
  if (typeof MediaRecorder === 'undefined') return false;
  return typeof navigator.mediaDevices?.getUserMedia === 'function';
}

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return undefined;
  }
  return PREFERRED_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
}

function extensionFor(mimeType: string): string {
  const base = mimeType.split(';')[0].trim().toLowerCase();
  if (base === 'audio/mp4') return 'm4a';
  if (base === 'audio/ogg') return 'ogg';
  if (base === 'audio/mpeg') return 'mp3';
  if (base === 'audio/wav' || base === 'audio/x-wav') return 'wav';
  return 'webm';
}

/** Human message for a getUserMedia / MediaRecorder failure. */
function describeCaptureError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone access was denied. Allow the microphone for this site and try again.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'No microphone was found.';
  }
  if (name === 'NotReadableError') {
    return 'The microphone is in use by another application.';
  }
  return 'Could not start recording.';
}

/**
 * Open the microphone: the ``default`` device by id first (see module
 * docstring for why), falling back to a bare request where that id does
 * not exist. Permission errors propagate from the first attempt.
 */
async function openMicrophone(): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: 'default' } } });
  } catch (error) {
    const name = error instanceof DOMException ? error.name : '';
    if (name !== 'OverconstrainedError' && name !== 'NotFoundError') throw error;
    return navigator.mediaDevices.getUserMedia({ audio: true });
  }
}

/** Root-mean-square of a time-domain sample buffer. */
function rmsOf(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / (samples.length || 1));
}

interface LevelMonitor {
  context: AudioContext;
  analyser: AnalyserNode;
  buffer: Float32Array<ArrayBuffer>;
}

/**
 * Attach an AnalyserNode to the stream so we can read input loudness.
 * Returns null when Web Audio is unavailable or refuses to run (a context
 * that stays suspended would only ever report zeros).
 */
async function createLevelMonitor(stream: MediaStream): Promise<LevelMonitor | null> {
  if (typeof AudioContext === 'undefined') return null;
  let context: AudioContext;
  try {
    context = new AudioContext();
  } catch {
    return null;
  }
  try {
    if (context.state !== 'running') await context.resume();
    if (context.state !== 'running') throw new Error('AudioContext not running');
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    return { context, analyser, buffer: new Float32Array(analyser.fftSize) };
  } catch {
    void context.close().catch(() => undefined);
    return null;
  }
}

interface UseVoiceRecorderOptions {
  /** Called with the finished clip after a manual stop or the auto-stop. */
  onRecordingComplete: (recording: VoiceRecording) => void;
  /** Called with a human-readable message when capture cannot start. */
  onError: (message: string) => void;
  maxSeconds?: number;
}

export function useVoiceRecorder({
  onRecordingComplete,
  onError,
  maxSeconds = MAX_RECORDING_SECONDS,
}: UseVoiceRecorderOptions) {
  const [status, setStatus] = useState<VoiceRecorderStatus>('idle');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [inputLevel, setInputLevel] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const monitorRef = useRef<LevelMonitor | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const maxTimerRef = useRef<number | null>(null);
  const liveTimeoutRef = useRef<number | null>(null);
  const levelFrameRef = useRef<number | null>(null);
  // True when stop() was reached via cancel(): the clip is discarded.
  const discardRef = useRef(false);
  // Set by stop()/cancel() while getUserMedia is still pending: start()
  // checks it once the stream arrives and aborts instead of recording.
  const abortStartRef = useRef(false);
  // True while start() is between the click and the recorder existing.
  const startingRef = useRef(false);
  // Latest callbacks without re-creating the recorder handlers.
  const onCompleteRef = useRef(onRecordingComplete);
  const onErrorRef = useRef(onError);
  useEffect(() => { onCompleteRef.current = onRecordingComplete; }, [onRecordingComplete]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  const clearTimers = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (maxTimerRef.current !== null) {
      window.clearTimeout(maxTimerRef.current);
      maxTimerRef.current = null;
    }
    if (liveTimeoutRef.current !== null) {
      window.clearTimeout(liveTimeoutRef.current);
      liveTimeoutRef.current = null;
    }
    if (levelFrameRef.current !== null) {
      window.cancelAnimationFrame(levelFrameRef.current);
      levelFrameRef.current = null;
    }
  }, []);

  const releaseStream = useCallback(() => {
    const monitor = monitorRef.current;
    monitorRef.current = null;
    if (monitor) {
      void monitor.context.close().catch(() => undefined);
    }
    const stream = streamRef.current;
    streamRef.current = null;
    if (stream) {
      for (const track of stream.getTracks()) {
        try { track.stop(); } catch { /* ignore */ }
      }
    }
    setInputLevel(0);
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder) {
      // Still waiting for getUserMedia: abort the start instead.
      if (startingRef.current) abortStartRef.current = true;
      return;
    }
    clearTimers();
    if (recorder.state !== 'inactive') {
      try {
        recorder.stop();
      } catch {
        // Already stopped or the stream vanished; the onstop handler (or
        // the release below) tidies up.
        recorderRef.current = null;
        releaseStream();
        setStatus('idle');
      }
    }
  }, [clearTimers, releaseStream]);

  const cancel = useCallback(() => {
    discardRef.current = true;
    stop();
  }, [stop]);

  const start = useCallback(async () => {
    if (recorderRef.current || startingRef.current || status !== 'idle') return;
    if (!isVoiceInputSupported()) {
      onErrorRef.current('Voice input is not available in this browser.');
      return;
    }
    startingRef.current = true;
    abortStartRef.current = false;
    setStatus('starting');
    let stream: MediaStream;
    try {
      stream = await openMicrophone();
    } catch (error) {
      startingRef.current = false;
      setStatus('idle');
      if (!abortStartRef.current) onErrorRef.current(describeCaptureError(error));
      return;
    }
    if (abortStartRef.current) {
      // stop()/cancel() arrived while the permission prompt or device open
      // was in flight: nothing was captured, nothing to deliver.
      startingRef.current = false;
      for (const track of stream.getTracks()) track.stop();
      setStatus('idle');
      return;
    }
    let recorder: MediaRecorder;
    try {
      const mimeType = pickMimeType();
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (error) {
      startingRef.current = false;
      for (const track of stream.getTracks()) track.stop();
      setStatus('idle');
      onErrorRef.current(describeCaptureError(error));
      return;
    }

    streamRef.current = stream;
    recorderRef.current = recorder;
    startingRef.current = false;
    chunksRef.current = [];
    discardRef.current = false;
    // Until the first audible frame, the clip's duration is counted from the
    // moment the recorder started; markLive() re-bases it.
    startedAtRef.current = Date.now();
    let live = false;

    const markLive = () => {
      if (live) return;
      live = true;
      if (liveTimeoutRef.current !== null) {
        window.clearTimeout(liveTimeoutRef.current);
        liveTimeoutRef.current = null;
      }
      startedAtRef.current = Date.now();
      setStatus('recording');
      setElapsedSeconds(0);
      timerRef.current = window.setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000));
      }, 500);
      maxTimerRef.current = window.setTimeout(() => stop(), maxSeconds * 1000);
    };

    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onerror = () => {
      discardRef.current = true;
      onErrorRef.current('Recording failed.');
      stop();
    };
    recorder.onstop = () => {
      const mimeType = recorder.mimeType || 'audio/webm';
      const chunks = chunksRef.current;
      const durationMs = Date.now() - startedAtRef.current;
      chunksRef.current = [];
      recorderRef.current = null;
      clearTimers();
      releaseStream();
      setStatus('idle');
      setElapsedSeconds(0);
      if (discardRef.current) return;
      const blob = new Blob(chunks, { type: mimeType });
      if (blob.size === 0) {
        onErrorRef.current('Nothing was recorded.');
        return;
      }
      onCompleteRef.current({
        blob,
        mimeType,
        filename: `voice-input.${extensionFor(mimeType)}`,
        durationMs,
      });
    };

    // A timeslice keeps data flowing so a tab crash mid-recording loses at
    // most a second, and Safari needs one to emit any data before stop.
    recorder.start(1000);
    // stop() may have been called synchronously by a re-render between the
    // await above and here (e.g. the composer locking); honour it.
    if (recorderRef.current !== recorder) return;

    // Watch the stream for the first real audio, then keep a level meter.
    const monitor = await createLevelMonitor(stream);
    if (recorderRef.current !== recorder) {
      if (monitor) void monitor.context.close().catch(() => undefined);
      return;
    }
    if (!monitor) {
      // No Web Audio: nothing to gate on, trust the recorder.
      markLive();
      return;
    }
    monitorRef.current = monitor;
    liveTimeoutRef.current = window.setTimeout(markLive, LIVE_SIGNAL_TIMEOUT_MS);
    let lastLevelAt = 0;
    let lastLevel = 0;
    const tick = () => {
      if (monitorRef.current !== monitor) return;
      monitor.analyser.getFloatTimeDomainData(monitor.buffer);
      const rms = rmsOf(monitor.buffer);
      if (!live && rms > LIVE_SIGNAL_RMS) markLive();
      const now = Date.now();
      if (live && now - lastLevelAt >= LEVEL_UPDATE_INTERVAL_MS) {
        lastLevelAt = now;
        const level = Math.round(Math.min(1, rms / FULL_SCALE_RMS) * LEVEL_STEPS) / LEVEL_STEPS;
        if (level !== lastLevel) {
          lastLevel = level;
          setInputLevel(level);
        }
      }
      levelFrameRef.current = window.requestAnimationFrame(tick);
    };
    levelFrameRef.current = window.requestAnimationFrame(tick);
  }, [status, maxSeconds, stop, clearTimers, releaseStream]);

  // Never leave the microphone open after unmount.
  useEffect(() => () => {
    discardRef.current = true;
    abortStartRef.current = true;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    clearTimers();
    if (recorder && recorder.state !== 'inactive') {
      try { recorder.stop(); } catch { /* ignore */ }
    }
    releaseStream();
  }, [clearTimers, releaseStream]);

  return { status, elapsedSeconds, inputLevel, start, stop, cancel };
}
