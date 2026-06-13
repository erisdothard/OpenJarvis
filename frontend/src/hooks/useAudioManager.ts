/**
 * Centralized audio playback manager.
 *
 * Owns ALL audio output (blob-URL queue for HTTP TTS, PCM scheduling for
 * WebSocket TTS) so every component shares one queue, one isPlaying flag,
 * and one interrupt mechanism.
 */

import { useCallback, useRef, useState, useEffect } from 'react';

export interface AudioManagerReturn {
  /** Enqueue a blob/direct URL for sequential playback.
   *  Pass `gen` from getGeneration() to drop stale requests after interrupt. */
  enqueueBlob: (url: string, gen?: number) => void;
  /** Enqueue a raw PCM float32 chunk for scheduled playback at 24 kHz. */
  enqueuePCM: (pcm: ArrayBuffer) => void;
  /** Stop all playback, revoke blob URLs, reset PCM scheduling. */
  interruptAll: () => void;
  /** Whether any audio is currently playing (blob or PCM). */
  isPlaying: boolean;
  /** Current output audio level 0–1 for orb/visualizer reactivity. */
  outputLevel: number;
  /** Capture the current generation before starting a TTS synthesis call.
   *  If interruptAll() fires before the blob arrives, enqueueBlob will
   *  silently drop it. */
  getGeneration: () => number;
}

export function useAudioManager(opts?: {
  onPlaybackStart?: () => void;
  onPlaybackFinished?: () => void;
}): AudioManagerReturn {
  const [isPlaying, setIsPlaying] = useState(false);
  const [outputLevel, setOutputLevel] = useState(0);

  const onStartRef = useRef(opts?.onPlaybackStart);
  const onFinishRef = useRef(opts?.onPlaybackFinished);
  onStartRef.current = opts?.onPlaybackStart;
  onFinishRef.current = opts?.onPlaybackFinished;

  // ── Internal state ──────────────────────────────────────────────────

  // Blob queue
  const blobQueueRef = useRef<string[]>([]);
  const currentBlobRef = useRef<HTMLAudioElement | null>(null);
  const drainingRef = useRef(false);

  // PCM scheduling
  const pcmCtxRef = useRef<AudioContext | null>(null);
  const pcmAnalyserRef = useRef<AnalyserNode | null>(null);
  const nextPcmTimeRef = useRef(0);
  const pcmActiveRef = useRef(false);
  const pcmEndTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Blob analyser (separate AudioContext for HTMLAudioElement routing)
  const blobAnalyserCtxRef = useRef<AudioContext | null>(null);
  const blobAnalyserRef = useRef<AnalyserNode | null>(null);
  const blobSourceRef = useRef<MediaElementAudioSourceNode | null>(null);

  // Shared
  const generationRef = useRef(0);
  const playingRef = useRef(false); // fast non-reactive mirror
  const rafRef = useRef(0);

  // ── Level monitor ───────────────────────────────────────────────────

  const stopLevelMonitor = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = 0;
    setOutputLevel(0);
  }, []);

  const startLevelMonitor = useCallback(() => {
    if (rafRef.current) return;
    const buf = new Uint8Array(128);
    const tick = () => {
      const analyser = pcmAnalyserRef.current || blobAnalyserRef.current;
      if (analyser) {
        analyser.getByteFrequencyData(buf);
        const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
        setOutputLevel(Math.min(rms / 80, 1));
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  // ── Helpers ─────────────────────────────────────────────────────────

  const markPlaying = useCallback(() => {
    if (!playingRef.current) {
      playingRef.current = true;
      setIsPlaying(true);
      onStartRef.current?.();
    }
  }, []);

  const markIdle = useCallback(() => {
    if (playingRef.current) {
      playingRef.current = false;
      setIsPlaying(false);
      stopLevelMonitor();
      onFinishRef.current?.();
    }
  }, [stopLevelMonitor]);

  // ── Blob queue drain ────────────────────────────────────────────────

  const drain = useCallback(() => {
    if (drainingRef.current) return;

    if (blobQueueRef.current.length === 0) {
      currentBlobRef.current = null;
      blobSourceRef.current?.disconnect();
      blobSourceRef.current = null;
      if (!pcmActiveRef.current) markIdle();
      return;
    }

    drainingRef.current = true;
    markPlaying();

    const url = blobQueueRef.current.shift()!;
    const audio = new Audio(url);
    currentBlobRef.current = audio;

    audio.onended = () => {
      if (url.startsWith('blob:')) URL.revokeObjectURL(url);
      blobSourceRef.current?.disconnect();
      blobSourceRef.current = null;
      currentBlobRef.current = null;
      drainingRef.current = false;
      drain();
    };

    audio.play().then(() => {
      try {
        if (!blobAnalyserCtxRef.current || blobAnalyserCtxRef.current.state === 'closed') {
          blobAnalyserCtxRef.current = new AudioContext();
          const a = blobAnalyserCtxRef.current.createAnalyser();
          a.fftSize = 256;
          blobAnalyserRef.current = a;
        }
        blobSourceRef.current?.disconnect();
        const source = blobAnalyserCtxRef.current.createMediaElementSource(audio);
        blobSourceRef.current = source;
        source.connect(blobAnalyserRef.current!);
        blobAnalyserRef.current!.connect(blobAnalyserCtxRef.current.destination);
        startLevelMonitor();
      } catch {
        // Level monitoring failed — audio still plays fine
      }
      drainingRef.current = false;
    }).catch(() => {
      if (url.startsWith('blob:')) URL.revokeObjectURL(url);
      drainingRef.current = false;
      drain();
    });
  }, [markPlaying, markIdle, startLevelMonitor]);

  // ── Public API ──────────────────────────────────────────────────────

  const enqueueBlob = useCallback((url: string, gen?: number) => {
    if (gen !== undefined && gen !== generationRef.current) {
      if (url.startsWith('blob:')) URL.revokeObjectURL(url);
      return;
    }
    blobQueueRef.current.push(url);
    if (!currentBlobRef.current && !drainingRef.current) drain();
  }, [drain]);

  const enqueuePCM = useCallback((pcm: ArrayBuffer) => {
    const samples = new Float32Array(pcm);
    if (samples.length === 0) return;

    if (!pcmCtxRef.current || pcmCtxRef.current.state === 'closed') {
      const ctx = new AudioContext({ sampleRate: 24000 });
      pcmCtxRef.current = ctx;
      const a = ctx.createAnalyser();
      a.fftSize = 256;
      a.connect(ctx.destination);
      pcmAnalyserRef.current = a;
    }

    const ctx = pcmCtxRef.current;
    const buf = ctx.createBuffer(1, samples.length, 24000);
    buf.getChannelData(0).set(samples);

    const src = ctx.createBufferSource();
    src.buffer = buf;
    if (pcmAnalyserRef.current) src.connect(pcmAnalyserRef.current);
    else src.connect(ctx.destination);

    const now = ctx.currentTime;
    const start = Math.max(now + 0.005, nextPcmTimeRef.current);
    src.start(start);
    nextPcmTimeRef.current = start + buf.duration;

    if (!pcmActiveRef.current) {
      pcmActiveRef.current = true;
      markPlaying();
      startLevelMonitor();
    }

    // Schedule a check for PCM completion (150 ms after last chunk ends)
    clearTimeout(pcmEndTimerRef.current);
    const endDelay = (nextPcmTimeRef.current - now) * 1000 + 150;
    pcmEndTimerRef.current = setTimeout(() => {
      if (pcmCtxRef.current && pcmActiveRef.current) {
        if (pcmCtxRef.current.currentTime >= nextPcmTimeRef.current) {
          pcmActiveRef.current = false;
          if (blobQueueRef.current.length === 0 && !currentBlobRef.current) markIdle();
        }
      }
    }, endDelay);
  }, [markPlaying, markIdle, startLevelMonitor]);

  const interruptAll = useCallback(() => {
    generationRef.current++;

    // Kill blob playback
    if (currentBlobRef.current) {
      currentBlobRef.current.pause();
      const src = currentBlobRef.current.src;
      if (src?.startsWith('blob:')) URL.revokeObjectURL(src);
      currentBlobRef.current = null;
    }
    for (const url of blobQueueRef.current) {
      if (url.startsWith('blob:')) URL.revokeObjectURL(url);
    }
    blobQueueRef.current = [];
    drainingRef.current = false;
    blobSourceRef.current?.disconnect();
    blobSourceRef.current = null;

    // Kill PCM playback (closing context stops all scheduled sources)
    clearTimeout(pcmEndTimerRef.current);
    if (pcmCtxRef.current) {
      pcmCtxRef.current.close().catch(() => {});
      pcmCtxRef.current = null;
    }
    pcmAnalyserRef.current = null;
    nextPcmTimeRef.current = 0;
    pcmActiveRef.current = false;

    // Kill blob analyser context
    if (blobAnalyserCtxRef.current) {
      blobAnalyserCtxRef.current.close().catch(() => {});
      blobAnalyserCtxRef.current = null;
    }
    blobAnalyserRef.current = null;

    playingRef.current = false;
    setIsPlaying(false);
    stopLevelMonitor();
  }, [stopLevelMonitor]);

  const getGeneration = useCallback(() => generationRef.current, []);

  // ── Cleanup on unmount ──────────────────────────────────────────────

  useEffect(() => {
    return () => {
      cancelAnimationFrame(rafRef.current);
      clearTimeout(pcmEndTimerRef.current);
      if (currentBlobRef.current) {
        currentBlobRef.current.pause();
        const src = currentBlobRef.current.src;
        if (src?.startsWith('blob:')) URL.revokeObjectURL(src);
      }
      for (const url of blobQueueRef.current) {
        if (url.startsWith('blob:')) URL.revokeObjectURL(url);
      }
      blobSourceRef.current?.disconnect();
      pcmCtxRef.current?.close().catch(() => {});
      blobAnalyserCtxRef.current?.close().catch(() => {});
    };
  }, []);

  return {
    enqueueBlob,
    enqueuePCM,
    interruptAll,
    isPlaying,
    outputLevel,
    getGeneration,
  };
}
