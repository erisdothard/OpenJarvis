import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type AlwaysOnState = 'idle' | 'monitoring' | 'capturing' | 'transcribing';

// VAD thresholds — tuned for real-world ambient noise
const SPEECH_START_THRESHOLD = 40; // RMS to detect speech onset (raised for ambient noise)
const SPEECH_END_THRESHOLD = 35; // slightly lower than start to avoid cutting off trailing words
const SILENCE_DURATION = 3000; // ms of silence before auto-transcribe (3 seconds)
const MIN_CAPTURE_MS = 500; // minimum recording before auto-stop can fire
const MAX_CAPTURE_MS = 15000; // safety: force-stop after 15s to prevent stuck state
const BARGE_IN_THRESHOLD = 45; // threshold to detect user speaking over Jarvis
const BARGE_IN_FRAMES = 2; // 2 frames ≈ 33ms at 60fps — near-instant

interface UseAlwaysOnVoiceOptions {
  enabled: boolean;
  jarvisSpeaking: boolean; // true when TTS is playing
  onTranscribed: (text: string) => void;
  onBargeIn: () => void;
  onAudioLevel: (rms: number) => void;
  onStateChange: (state: AlwaysOnState) => void;
}

interface UseAlwaysOnVoiceReturn {
  state: AlwaysOnState;
  error: string | null;
  available: boolean;
  activate: () => Promise<void>;
  deactivate: () => void;
}

export function useAlwaysOnVoice(options: UseAlwaysOnVoiceOptions): UseAlwaysOnVoiceReturn {
  const [state, setState] = useState<AlwaysOnState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);

  // Keep options in refs to avoid re-creating loops
  const optsRef = useRef(options);
  optsRef.current = options;

  // Persistent resources
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const rafRef = useRef<number | null>(null);
  const stateRef = useRef<AlwaysOnState>('idle');
  const captureStartRef = useRef(0);
  const silentSinceRef = useRef<number | null>(null);
  const bargeInFramesRef = useRef(0);
  const activeRef = useRef(false);

  // Check backend availability
  useEffect(() => {
    fetchSpeechHealth()
      .then((h) => setAvailable(h.available))
      .catch(() => setAvailable(false));
  }, []);

  const updateState = useCallback((s: AlwaysOnState) => {
    stateRef.current = s;
    setState(s);
    optsRef.current.onStateChange(s);
  }, []);

  const stopRecorder = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      recorderRef.current.stop();
    }
    recorderRef.current = null;
  }, []);

  const startCapture = useCallback(() => {
    if (!streamRef.current || stateRef.current === 'capturing') return;

    chunksRef.current = [];
    const recorder = new MediaRecorder(streamRef.current);
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = async () => {
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || 'audio/webm',
      });
      chunksRef.current = [];

      if (blob.size < 1000) {
        // Too small to be meaningful speech
        if (activeRef.current) updateState('monitoring');
        return;
      }

      updateState('transcribing');

      try {
        const result = await transcribeAudio(blob);
        if (result.text?.trim()) {
          optsRef.current.onTranscribed(result.text.trim());
        }
      } catch {
        // Transcription failed — just resume monitoring
      }

      if (activeRef.current) updateState('monitoring');
    };

    recorder.start(250);
    recorderRef.current = recorder;
    captureStartRef.current = Date.now();
    silentSinceRef.current = null;
    updateState('capturing');
  }, [updateState]);

  const runLoop = useCallback(() => {
    if (!activeRef.current || !analyserRef.current) return;

    const analyser = analyserRef.current;
    const data = new Uint8Array(analyser.frequencyBinCount);

    const tick = () => {
      if (!activeRef.current) return;

      analyser.getByteFrequencyData(data);
      const rms = Math.sqrt(
        data.reduce((sum, val) => sum + val * val, 0) / data.length,
      );

      // Normalize to 0-1 for the orb
      optsRef.current.onAudioLevel(Math.min(rms / 80, 1));

      const now = Date.now();
      const currentState = stateRef.current;
      const isSpeaking = optsRef.current.jarvisSpeaking;

      if (currentState === 'monitoring') {
        const threshold = isSpeaking ? BARGE_IN_THRESHOLD : SPEECH_START_THRESHOLD;

        if (rms > threshold) {
          if (isSpeaking) {
            bargeInFramesRef.current++;
            if (bargeInFramesRef.current >= BARGE_IN_FRAMES) {
              bargeInFramesRef.current = 0;
              optsRef.current.onBargeIn();
              startCapture();
            }
          } else {
            bargeInFramesRef.current = 0;
            startCapture();
          }
        } else {
          bargeInFramesRef.current = 0;
        }
      } else if (currentState === 'capturing') {
        const elapsed = now - captureStartRef.current;

        // Safety: force-stop if capture runs too long (prevents stuck state)
        if (elapsed > MAX_CAPTURE_MS) {
          stopRecorder();
          return;
        }

        if (rms < SPEECH_END_THRESHOLD) {
          if (silentSinceRef.current === null) silentSinceRef.current = now;
          if (
            elapsed > MIN_CAPTURE_MS &&
            now - silentSinceRef.current >= SILENCE_DURATION
          ) {
            stopRecorder();
            return; // onstop handler will resume monitoring
          }
        } else {
          silentSinceRef.current = null;
        }
      }
      // transcribing state: just keep the loop alive, don't analyze

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
  }, [startCapture, stopRecorder]);

  const activate = useCallback(async () => {
    if (activeRef.current) return;

    setError(null);

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      const ctx = new AudioContext();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;

      activeRef.current = true;
      updateState('monitoring');
      runLoop();
    } catch {
      setError('Microphone access denied');
      updateState('idle');
    }
  }, [updateState, runLoop]);

  const deactivate = useCallback(() => {
    activeRef.current = false;

    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    stopRecorder();

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }

    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    bargeInFramesRef.current = 0;

    updateState('idle');
    optsRef.current.onAudioLevel(0);
  }, [updateState, stopRecorder]);

  // Auto-activate/deactivate when enabled changes
  useEffect(() => {
    if (options.enabled && !activeRef.current) {
      activate();
    } else if (!options.enabled && activeRef.current) {
      deactivate();
    }
  }, [options.enabled, activate, deactivate]);

  // Pause on tab hidden, resume on visible
  useEffect(() => {
    if (!options.enabled) return;

    const handleVisibility = () => {
      if (document.hidden) {
        if (activeRef.current) {
          // Pause the loop but keep the stream alive
          if (rafRef.current) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          stopRecorder();
        }
      } else {
        if (activeRef.current && !rafRef.current) {
          updateState('monitoring');
          runLoop();
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, [options.enabled, runLoop, stopRecorder, updateState]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      activeRef.current = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close().catch(() => {});
    };
  }, []);

  return { state, error, available, activate, deactivate };
}
