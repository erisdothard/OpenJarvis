/**
 * WebSocket voice streaming hook.
 *
 * Streams raw PCM mic audio to the server, which handles VAD, STT, LLM
 * inference, and TTS.  Audio chunks and control messages come back over the
 * same WebSocket.
 *
 * Playback is delegated to the caller via the onAudioData / onStopPlayback
 * callbacks (typically wired to useAudioManager).
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
  /** Audio level 0–1 (mic input when listening). */
  onAudioLevel?: (level: number) => void;
  onToolStart?: (tool: string, args: string) => void;
  onToolEnd?: (tool: string, success: boolean, latency: number) => void;
  onError?: (detail: string) => void;
  /** Called when a binary PCM float32 audio chunk arrives from TTS. */
  onAudioData?: (pcm: ArrayBuffer) => void;
  /** Called when playback should stop (barge-in, explicit server stop, disconnect). */
  onStopPlayback?: () => void;
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

  const optRef = useRef(options);
  optRef.current = options;

  // ── Refs for WebSocket + mic plumbing ──
  const wsRef = useRef<WebSocket | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);

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

  // ── Mic audio level ticker ──
  const startLevelMonitor = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    const buf = new Uint8Array(128);
    const tick = () => {
      const s = stateRef.current;
      if ((s === 'idle' || s === 'listening') && micAnalyserRef.current) {
        micAnalyserRef.current.getByteFrequencyData(buf);
        const rms = Math.sqrt(buf.reduce((a, v) => a + v * v, 0) / buf.length);
        optRef.current.onAudioLevel?.(Math.min(rms / 128, 1));
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const stopLevelMonitor = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    optRef.current.onAudioLevel?.(0);
  }, []);

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
          break;
        case 'vad.speech_start':
          optRef.current.onStopPlayback?.();
          break;
        case 'stop_playback':
          optRef.current.onStopPlayback?.();
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
      // Binary frame — PCM float32 audio from TTS
      if (ev.data instanceof ArrayBuffer) {
        optRef.current.onAudioData?.(ev.data);
      } else if (ev.data instanceof Blob) {
        ev.data.arrayBuffer().then((buf) => optRef.current.onAudioData?.(buf));
      }
    }
  }, [notify]);

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
    optRef.current.onStopPlayback?.();
    stopLevelMonitor();
    notify('disconnected');
  }, [stopLevelMonitor, notify]);

  // ── Interrupt (stop TTS, reset server pipeline) ──
  const interrupt = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'interrupt' }));
    }
    optRef.current.onStopPlayback?.();
  }, []);

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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;

      const micCtx = new AudioContext();
      micCtxRef.current = micCtx;
      await micCtx.audioWorklet.addModule('/mic-capture-processor.js');

      const micSource = micCtx.createMediaStreamSource(stream);
      const analyser = micCtx.createAnalyser();
      analyser.fftSize = 256;
      micAnalyserRef.current = analyser;

      const worklet = new AudioWorkletNode(micCtx, 'mic-capture-processor');
      workletRef.current = worklet;

      micSource.connect(analyser);
      analyser.connect(worklet);

      const ws = new WebSocket(getVoiceWsUrl());
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

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
