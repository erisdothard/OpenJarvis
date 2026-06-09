import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

const SILENCE_THRESHOLD = 15; // RMS level (0-255) below which we consider silence
const BARGE_IN_THRESHOLD = 30; // Higher threshold to avoid speaker bleed triggering barge-in
const SILENCE_DURATION = 1500; // ms of continuous silence before auto-stop
const MIN_RECORDING_MS = 500; // don't auto-stop before this

interface UseSpeechOptions {
  onTranscribed?: (text: string) => void;
}

export function useSpeech(options?: UseSpeechOptions) {
  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rafRef = useRef<number | null>(null);
  const recordingStartRef = useRef<number>(0);
  const onTranscribedRef = useRef(options?.onTranscribed);
  onTranscribedRef.current = options?.onTranscribed;

  // Barge-in monitor refs
  const bargeInRafRef = useRef<number | null>(null);
  const bargeInContextRef = useRef<AudioContext | null>(null);
  const bargeInStreamRef = useRef<MediaStream | null>(null);

  // Check if speech backend is available on mount
  useEffect(() => {
    fetchSpeechHealth()
      .then((health) => setAvailable(health.available))
      .catch(() => setAvailable(false));
  }, []);

  const cleanupVAD = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (vadTimerRef.current) {
      clearTimeout(vadTimerRef.current);
      vadTimerRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    analyserRef.current = null;
  }, []);

  const stopBargeInMonitor = useCallback(() => {
    if (bargeInRafRef.current) {
      cancelAnimationFrame(bargeInRafRef.current);
      bargeInRafRef.current = null;
    }
    if (bargeInStreamRef.current) {
      bargeInStreamRef.current.getTracks().forEach((t) => t.stop());
      bargeInStreamRef.current = null;
    }
    if (bargeInContextRef.current) {
      bargeInContextRef.current.close().catch(() => {});
      bargeInContextRef.current = null;
    }
  }, []);

  const startBargeInMonitor = useCallback(
    (onBargeIn: () => void) => {
      // Don't monitor if already recording
      if (state === 'recording' || state === 'transcribing') return;

      stopBargeInMonitor();

      navigator.mediaDevices
        .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
        .then((stream) => {
          bargeInStreamRef.current = stream;
          const ctx = new AudioContext();
          bargeInContextRef.current = ctx;
          const source = ctx.createMediaStreamSource(stream);
          const analyser = ctx.createAnalyser();
          analyser.fftSize = 512;
          source.connect(analyser);

          const dataArray = new Uint8Array(analyser.frequencyBinCount);
          let speechFrames = 0;

          const check = () => {
            if (!bargeInContextRef.current) return;
            analyser.getByteFrequencyData(dataArray);
            const rms = Math.sqrt(
              dataArray.reduce((sum, val) => sum + val * val, 0) / dataArray.length,
            );

            if (rms > BARGE_IN_THRESHOLD) {
              speechFrames++;
              // Require 3 consecutive frames of speech to avoid false triggers
              if (speechFrames >= 3) {
                stopBargeInMonitor();
                onBargeIn();
                return;
              }
            } else {
              speechFrames = 0;
            }
            bargeInRafRef.current = requestAnimationFrame(check);
          };
          bargeInRafRef.current = requestAnimationFrame(check);
        })
        .catch(() => {
          // Mic not available — barge-in won't work, but that's fine
        });
    },
    [state, stopBargeInMonitor],
  );

  const doStop = useCallback(async (): Promise<string> => {
    return new Promise((resolve, reject) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state !== 'recording') {
        reject(new Error('Not recording'));
        return;
      }

      cleanupVAD();

      recorder.onstop = async () => {
        setState('transcribing');

        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;

        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        chunksRef.current = [];

        try {
          const result = await transcribeAudio(blob);
          setState('idle');
          resolve(result.text);
        } catch (err) {
          setState('idle');
          const msg = err instanceof Error ? err.message : 'Transcription failed';
          setError(msg);
          reject(err);
        }
      };

      recorder.stop();
    });
  }, [cleanupVAD]);

  const startRecording = useCallback(async (): Promise<void> => {
    setError(null);
    stopBargeInMonitor();

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start(250);
      mediaRecorderRef.current = recorder;
      recordingStartRef.current = Date.now();
      setState('recording');

      // Set up VAD with Web Audio API
      const audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      audioContextRef.current = audioContext;
      analyserRef.current = analyser;

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      let silentSince: number | null = null;

      const checkVolume = () => {
        if (!analyserRef.current || !mediaRecorderRef.current || mediaRecorderRef.current.state !== 'recording') {
          return;
        }

        analyserRef.current.getByteFrequencyData(dataArray);
        const rms = Math.sqrt(
          dataArray.reduce((sum, val) => sum + val * val, 0) / dataArray.length,
        );

        const now = Date.now();
        const elapsed = now - recordingStartRef.current;

        if (rms < SILENCE_THRESHOLD) {
          if (silentSince === null) silentSince = now;
          if (elapsed > MIN_RECORDING_MS && now - silentSince >= SILENCE_DURATION) {
            doStop()
              .then((text) => {
                if (text && onTranscribedRef.current) {
                  onTranscribedRef.current(text);
                }
              })
              .catch(() => {});
            return;
          }
        } else {
          silentSince = null;
        }

        rafRef.current = requestAnimationFrame(checkVolume);
      };

      rafRef.current = requestAnimationFrame(checkVolume);
    } catch {
      setError('Microphone access denied');
      setState('idle');
    }
  }, [doStop, stopBargeInMonitor]);

  const stopRecording = useCallback(async (): Promise<string> => {
    return doStop();
  }, [doStop]);

  return {
    state,
    error,
    available,
    startRecording,
    stopRecording,
    startBargeInMonitor,
    stopBargeInMonitor,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
  };
}
