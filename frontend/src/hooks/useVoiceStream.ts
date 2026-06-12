/**
 * WebSocket voice streaming hook.
 *
 * Replaces the record→upload→transcribe→LLM→TTS relay with a single
 * persistent WebSocket that streams raw PCM both ways:
 *
 *   Mic PCM → Server VAD → faster-whisper → LLM → Cartesia TTS → Speaker
 *
 * When the server-side Silero VAD + STT backend aren't available the hook
 * reports `available === false` and callers should fall back to the legacy
 * useSpeech + HTTP streaming path.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { fetchVoiceStreamHealth, getVoiceWsUrl } from '../lib/api';

// ── Types ──────────────────────────────────────────────────────────────────

export type VoiceStreamState =
  | 'disconnected'
  | 'connecting'
  | 'idle'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking';

export interface UseVoiceStreamOptions {
  onTranscript?: (text: string, isFinal: boolean) => void;
  onLlmDelta?: (delta: string) => void;
  onLlmDone?: (content: string) => void;
  onStateChange?: (state: VoiceStreamState) => void;
  /** Audio level 0–1 (mic input when listening, TTS output when speaking). */
  onAudioLevel?: (level: number) => void;
  onToolStart?: (tool: string, args: string) => void;
  onToolEnd?: (tool: string, success: boolean, latency: number) => void;
  onError?: (detail: string) => void;
}

export interface UseVoiceStreamReturn {
  state: VoiceStreamState;
  /** null = still checking, true/false = result */
  available: boolean | null;
  connect: (config?: { model?: string; voice_id?: string }) => Promise<void>;
  disconnect: () => void;
  interrupt: () => void;
  /** Force end-of-speech (e.g. user pressed "send" while speaking). */
  commit: () => void;
}

// ── Hook ───────────────────────────────────────────────────────────────────

export function useVoiceStream(options: UseVoiceStreamOptions): UseVoiceStreamReturn {
  const [state, setState] = useState<VoiceStreamState>('disconnected');
  const [available, setAvailable] = useState<boolean | null>(null);

  // Stable ref to latest callbacks so closures never go stale.
  const optRef = useRef(options);
  optRef.current = options;

  // ── Refs for WebSocket + Audio plumbing ──
  const wsRef = useRef<WebSocket | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);

  // Playback
  const playCtxRef = useRef<AudioContext | null>(null);
  const playAnalyserRef = useRef<AnalyserNode | null>(null);
  const nextPlayTimeRef = useRef(0);
  const playingRef = useRef(false);

  // Level monitor
  const rafRef = useRef(0);
  const stateRef = useRef<VoiceStreamState>('disconnected');

  // ── Health check ──
  useEffect(() => {
    let cancelled = false;
    fetchVoiceStreamHealth().then((h) => {
      if (!cancelled) setAvailable(h.available);
    });
    return () => { cancelled = true; };
  }, []);

  // ── State helper ──
  const notify = useCallback((s: VoiceStreamState) => {
    stateRef.current = s;
    setState(s);
    optRef.current.onStateChange?.(s);
  }, []);

  // ── Audio level ticker ──
  const startLevelMonitor = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    const micBuf = new Uint8Array(128);
    const playBuf = new Uint8Array(128);
    const tick = () => {
      const s = stateRef.current;
      let level = 0;
      if (s === 'speaking' && playAnalyserRef.current) {
        playAnalyserRef.current.getByteFrequencyData(playBuf);
        const rms = Math.sqrt(playBuf.reduce((a, v) => a + v * v, 0) / playBuf.length);
        level = Math.min(rms / 80, 1);
      } else if ((s === 'idle' || s === 'listening') && micAnalyserRef.current) {
        micAnalyserRef.current.getByteFrequencyData(micBuf);
        const rms = Math.sqrt(micBuf.reduce((a, v) => a + v * v, 0) / micBuf.length);
        level = Math.min(rms / 128, 1);
      }
      optRef.current.onAudioLevel?.(level);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const stopLevelMonitor = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    optRef.current.onAudioLevel?.(0);
  }, []);

  // ── Playback helpers ──
  const stopPlayback = useCallback(() => {
    playingRef.current = false;
    if (playCtxRef.current) {
      playCtxRef.current.close().catch(() => {});
      playCtxRef.current = null;
    }
    playAnalyserRef.current = null;
    nextPlayTimeRef.current = 0;
  }, []);

  const ensurePlayCtx = useCallback((): AudioContext => {
    if (!playCtxRef.current || playCtxRef.current.state === 'closed') {
      const ctx = new AudioContext({ sampleRate: 24000 });
      playCtxRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.connect(ctx.destination);
      playAnalyserRef.current = analyser;
    }
    return playCtxRef.current;
  }, []);

  const playChunk = useCallback((pcm: ArrayBuffer) => {
    const ctx = ensurePlayCtx();
    const samples = new Float32Array(pcm);
    if (samples.length === 0) return;

    const buf = ctx.createBuffer(1, samples.length, 24000);
    buf.getChannelData(0).set(samples);

    const src = ctx.createBufferSource();
    src.buffer = buf;
    if (playAnalyserRef.current) src.connect(playAnalyserRef.current);
    else src.connect(ctx.destination);

    const now = ctx.currentTime;
    const start = Math.max(now + 0.005, nextPlayTimeRef.current);
    src.start(start);
    nextPlayTimeRef.current = start + buf.duration;
    playingRef.current = true;
  }, [ensurePlayCtx]);

  // ── WebSocket message handler ──
  const handleMsg = useCallback((ev: MessageEvent) => {
    if (typeof ev.data === 'string') {
      let msg: Record<string, unknown>;
      try { msg = JSON.parse(ev.data); } catch { return; }

      switch (msg.type) {
        case 'session.started':
          notify('idle');
          break;
        case 'state':
          notify(msg.state as VoiceStreamState);
          break;
        case 'transcript':
          optRef.current.onTranscript?.(msg.text as string, msg.is_final as boolean);
          break;
        case 'llm.delta':
          optRef.current.onLlmDelta?.(msg.content as string);
          break;
        case 'llm.done':
          optRef.current.onLlmDone?.(msg.content as string);
          break;
        case 'tts.start':
          notify('speaking');
          break;
        case 'tts.done':
          // TTS chunk finished — stay in current state (more chunks may follow,
          // or the server will send state:idle when the full response is done)
          break;
        case 'vad.speech_start':
          // Barge-in: user started talking again — kill TTS playback immediately
          stopPlayback();
          break;
        case 'stop_playback':
          // Server explicitly requests playback stop (interrupt / barge-in)
          stopPlayback();
          break;
        case 'tool.start':
          optRef.current.onToolStart?.(msg.tool as string, msg.arguments as string);
          break;
        case 'tool.end':
          optRef.current.onToolEnd?.(msg.tool as string, msg.success as boolean, msg.latency as number);
          break;
        case 'error':
          optRef.current.onError?.(msg.detail as string);
          break;
      }
    } else {
      // Binary frame — PCM float32 audio from Cartesia TTS
      if (ev.data instanceof ArrayBuffer) {
        playChunk(ev.data);
      } else if (ev.data instanceof Blob) {
        ev.data.arrayBuffer().then(playChunk);
      }
    }
  }, [notify, playChunk, stopPlayback]);

  // ── Disconnect ──
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN) {
        try { wsRef.current.send(JSON.stringify({ type: 'session.end' })); } catch {}
      }
      wsRef.current.close();
      wsRef.current = null;
    }
    workletRef.current?.disconnect();
    workletRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    micAnalyserRef.current = null;
    micCtxRef.current?.close().catch(() => {});
    micCtxRef.current = null;
    stopPlayback();
    stopLevelMonitor();
    notify('disconnected');
  }, [stopPlayback, stopLevelMonitor, notify]);

  // ── Interrupt (stop TTS, reset server pipeline) ──
  const interrupt = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'interrupt' }));
    }
    stopPlayback();
  }, [stopPlayback]);

  // ── Force commit speech ──
  const commit = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'commit' }));
    }
  }, []);

  // ── Connect ──
  const connect = useCallback(async (config?: { model?: string; voice_id?: string }) => {
    if (wsRef.current) disconnect();
    notify('connecting');

    try {
      // 1. Get mic permission
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;

      // 2. AudioContext + WorkletNode
      const micCtx = new AudioContext();
      micCtxRef.current = micCtx;
      await micCtx.audioWorklet.addModule('/mic-capture-processor.js');

      const micSource = micCtx.createMediaStreamSource(stream);
      const analyser = micCtx.createAnalyser();
      analyser.fftSize = 256;
      micAnalyserRef.current = analyser;

      const worklet = new AudioWorkletNode(micCtx, 'mic-capture-processor');
      workletRef.current = worklet;

      // source → analyser → worklet (no destination — don't echo mic)
      micSource.connect(analyser);
      analyser.connect(worklet);

      // 3. Open WebSocket
      const ws = new WebSocket(getVoiceWsUrl());
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      // Pipe PCM chunks to server
      worklet.port.onmessage = (e) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
      };

      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Connection timeout')), 10_000);
        ws.onopen = () => {
          clearTimeout(timer);
          ws.send(JSON.stringify({
            type: 'session.start',
            config: { model: config?.model, voice_id: config?.voice_id },
          }));
          resolve();
        };
        ws.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket failed')); };
      });

      ws.onmessage = handleMsg;
      ws.onclose = () => { if (wsRef.current === ws) disconnect(); };

      // 4. Start level ticker
      startLevelMonitor();

    } catch (err: unknown) {
      disconnect();
      const msg = err instanceof Error ? err.message : 'Connection failed';
      optRef.current.onError?.(msg);
      throw err;
    }
  }, [disconnect, notify, handleMsg, startLevelMonitor]);

  // Cleanup on unmount
  useEffect(() => () => { disconnect(); }, [disconnect]);

  return { state, available, connect, disconnect, interrupt, commit };
}
