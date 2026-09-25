/**
 * Microphone capture for the composer's voice input.
 *
 * Wraps ``getUserMedia`` + ``MediaRecorder`` into a tiny state machine
 * (idle -> recording -> idle) and hands the finished clip to the caller as
 * a Blob; the caller (Composer.tsx) uploads it to ``POST /transcribe`` and
 * drops the transcript into the textarea. The hook never touches the
 * network itself.
 *
 * Browser reality this accounts for:
 * - Capture needs a secure context (https or localhost); on a plain-http
 *   dev instance reached by IP, ``navigator.mediaDevices`` is undefined.
 *   ``isVoiceInputSupported()`` is the feature check the composer uses to
 *   hide the button entirely rather than show one that cannot work.
 * - Chrome/Firefox/Edge record Opus-in-WebM; Safari records AAC-in-MP4.
 *   The first supported entry of ``PREFERRED_MIME_TYPES`` wins, falling
 *   back to the browser's default; the server sniffs the container anyway.
 * - Recordings auto-stop at ``MAX_RECORDING_SECONDS`` (mirrored in
 *   chat/transcription.py) so a clip can never exceed the upload cap.
 * - Tracks are stopped as soon as the recording ends so the browser's
 *   "microphone in use" indicator goes away immediately.
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

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const maxTimerRef = useRef<number | null>(null);
  // True when stop() was reached via cancel(): the clip is discarded.
  const discardRef = useRef(false);
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
  }, []);

  const releaseStream = useCallback(() => {
    const stream = streamRef.current;
    streamRef.current = null;
    if (stream) {
      for (const track of stream.getTracks()) {
        try { track.stop(); } catch { /* ignore */ }
      }
    }
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder) return;
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
    if (recorderRef.current || status !== 'idle') return;
    if (!isVoiceInputSupported()) {
      onErrorRef.current('Voice input is not available in this browser.');
      return;
    }
    setStatus('starting');
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      setStatus('idle');
      onErrorRef.current(describeCaptureError(error));
      return;
    }
    let recorder: MediaRecorder;
    try {
      const mimeType = pickMimeType();
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (error) {
      for (const track of stream.getTracks()) track.stop();
      setStatus('idle');
      onErrorRef.current(describeCaptureError(error));
      return;
    }

    streamRef.current = stream;
    recorderRef.current = recorder;
    chunksRef.current = [];
    discardRef.current = false;

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

    startedAtRef.current = Date.now();
    // A timeslice keeps data flowing so a tab crash mid-recording loses at
    // most a second, and Safari needs one to emit any data before stop.
    recorder.start(1000);
    setStatus('recording');
    setElapsedSeconds(0);
    timerRef.current = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 500);
    maxTimerRef.current = window.setTimeout(() => stop(), maxSeconds * 1000);
  }, [status, maxSeconds, stop, clearTimers, releaseStream]);

  // Never leave the microphone open after unmount.
  useEffect(() => () => {
    discardRef.current = true;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    clearTimers();
    if (recorder && recorder.state !== 'inactive') {
      try { recorder.stop(); } catch { /* ignore */ }
    }
    releaseStream();
  }, [clearTimers, releaseStream]);

  return { status, elapsedSeconds, start, stop, cancel };
}
