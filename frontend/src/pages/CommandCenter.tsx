import { useCallback, useEffect, useRef, useState } from 'react';
import { Send, Square, Play, VolumeX, RefreshCw, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore, generateId } from '../lib/store';
import {
  fetchManagedAgents,
  fetchPendingApprovals,
  fetchServerInfo,
  fetchSavings,
  approveAction,
  denyAction,
  synthesizeSpeech,

} from '../lib/api';
import { listConnectors } from '../lib/connectors-api';
import { streamChat, streamResearch } from '../lib/sse';
import { useBriefing } from '../hooks/useBriefing';
import { useSpeech } from '../hooks/useSpeech';
import { useAlwaysOnVoice } from '../hooks/useAlwaysOnVoice';
import { JarvisOrb } from '../components/JarvisOrb/JarvisOrb';
import type { ManagedAgent, PendingApproval } from '../lib/api';
import type {
  ChatMessage,
  MessageTelemetry,
  ResearchSearchTrace,
  ResearchSource,
  TokenUsage,
  ToolCallInfo,
} from '../types';

/* ── helpers ── */

function timeAgo(epoch: number): string {
  const diff = Date.now() - epoch * 1000;
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function agentDotClass(status: string): string {
  switch (status) {
    case 'running': return 'pt-dot-run';
    case 'error':
    case 'budget_exceeded': return 'pt-dot-error';
    case 'paused':
    case 'needs_attention':
    case 'stalled': return 'pt-dot-warn';
    default: return 'pt-dot-idle';
  }
}

function agentLabel(status: string): string {
  const map: Record<string, string> = {
    idle: 'idle', running: 'running', paused: 'paused',
    error: 'error', needs_attention: 'attention', stalled: 'stalled',
    budget_exceeded: 'budget', archived: 'archived',
  };
  return map[status] || 'idle';
}

/* ── Main ── */

export function CommandCenter() {
  const serverInfo = useAppStore((s) => s.serverInfo);
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const streamState = useAppStore((s) => s.streamState);
  const briefing = useAppStore((s) => s.briefing);
  // const savings = useAppStore((s) => s.savings);
  const messages = useAppStore((s) => s.messages);
  const activeId = useAppStore((s) => s.activeId);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const voiceAlwaysOn = useAppStore((s) => s.settings.voiceAlwaysOn);
  const maxTokens = useAppStore((s) => s.settings.maxTokens);
  const temperature = useAppStore((s) => s.settings.temperature);
  const jarvisState = useAppStore((s) => s.jarvisState);
  const audioLevel = useAppStore((s) => s.audioLevel);
  const setJarvisState = useAppStore((s) => s.setJarvisState);
  const setAudioLevel = useAppStore((s) => s.setAudioLevel);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const createConversation = useAppStore((s) => s.createConversation);
  const addMessage = useAppStore((s) => s.addMessage);
  const updateLastAssistant = useAppStore((s) => s.updateLastAssistant);
  const setStreamState = useAppStore((s) => s.setStreamState);
  const resetStream = useAppStore((s) => s.resetStream);
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen);

  const [agents, setAgents] = useState<ManagedAgent[]>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [processing, setProcessing] = useState<Record<string, boolean>>({});

  const [input, setInput] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastResponseRef = useRef('');

  // Audio refs for TTS
  const audioQueueRef = useRef<HTMLAudioElement[]>([]);
  const playingRef = useRef(false);
  const sentenceBufferRef = useRef('');
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const startRecordingRef = useRef<(() => Promise<void>) | undefined>(undefined);
  const interruptAudioRef = useRef<() => void>(() => {});
  const stopBargeInRef = useRef<() => void>(() => {});
  const startBargeInRef = useRef<(cb: () => void) => void>(() => {});
  const voiceInitiatedRef = useRef(false);
  const pendingVoiceRef = useRef(false);

  const { interrupt, refresh: refreshBriefing, play: playBriefing } = useBriefing();

  // ── Data polling ──
  const refresh = useCallback(async () => {
    const [a, ap] = await Promise.allSettled([
      fetchManagedAgents(),
      fetchPendingApprovals(),
    ]);
    if (a.status === 'fulfilled') setAgents(a.value);
    if (ap.status === 'fulfilled') setApprovals(ap.value);
  }, []);

  useEffect(() => {
    fetchServerInfo().then((info) => useAppStore.getState().setServerInfo(info)).catch(() => {});
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  // ── Approval handlers ──
  const handleApprove = async (id: string) => {
    setProcessing((p) => ({ ...p, [id]: true }));
    try { await approveAction(id); setApprovals((prev) => prev.filter((a) => a.id !== id)); }
    finally { setProcessing((p) => ({ ...p, [id]: false })); }
  };
  const handleDeny = async (id: string) => {
    setProcessing((p) => ({ ...p, [id]: true }));
    try { await denyAction(id); setApprovals((prev) => prev.filter((a) => a.id !== id)); }
    finally { setProcessing((p) => ({ ...p, [id]: false })); }
  };

  // Audio output level analysis ref
  const ttsAnalyserRef = useRef<{ ctx: AudioContext; analyser: AnalyserNode; raf: number } | null>(null);

  const stopTTSAnalyser = useCallback(() => {
    if (ttsAnalyserRef.current) {
      cancelAnimationFrame(ttsAnalyserRef.current.raf);
      ttsAnalyserRef.current.ctx.close().catch(() => {});
      ttsAnalyserRef.current = null;
    }
  }, []);

  // ── TTS plumbing ──
  const playNextAudio = useCallback(() => {
    if (audioQueueRef.current.length === 0) {
      playingRef.current = false;
      currentAudioRef.current = null;
      stopTTSAnalyser();
      setAudioLevel(0);
      stopBargeInRef.current();
      voiceInitiatedRef.current = false;
      // Only reset to idle when streaming is ALSO done — otherwise stay
      // in 'thinking' so the orb/status doesn't flicker between TTS sentences.
      const stillStreaming = useAppStore.getState().streamState.isStreaming;
      if (!stillStreaming) {
        setJarvisState('idle');
      }
      return;
    }
    playingRef.current = true;
    setJarvisState('speaking');
    const audio = audioQueueRef.current.shift()!;
    currentAudioRef.current = audio;
    audio.onended = () => {
      URL.revokeObjectURL(audio.src);
      currentAudioRef.current = null;
      stopTTSAnalyser();
      playNextAudio();
    };
    audio.play().then(() => {
      // Extract audio levels from TTS output for orb reactivity
      try {
        const ctx = new AudioContext();
        const source = ctx.createMediaElementSource(audio);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyser.connect(ctx.destination);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          if (!playingRef.current) return;
          analyser.getByteFrequencyData(data);
          const rms = Math.sqrt(data.reduce((s, v) => s + v * v, 0) / data.length);
          setAudioLevel(Math.min(rms / 80, 1));
          ttsAnalyserRef.current = { ctx, analyser, raf: requestAnimationFrame(tick) };
        };
        ttsAnalyserRef.current = { ctx, analyser, raf: requestAnimationFrame(tick) };
      } catch {
        // AudioContext not available — orb won't react to output, but TTS still plays
      }
    }).catch(() => playNextAudio());
    if (!voiceAlwaysOn) {
      startBargeInRef.current(() => {
        interruptAudioRef.current();
      });
    }
  }, [voiceAlwaysOn, setJarvisState, setAudioLevel, stopTTSAnalyser]);

  const interruptAudio = useCallback(() => {
    stopBargeInRef.current();
    stopTTSAnalyser();
    if (currentAudioRef.current) { currentAudioRef.current.pause(); URL.revokeObjectURL(currentAudioRef.current.src); currentAudioRef.current = null; }
    for (const a of audioQueueRef.current) URL.revokeObjectURL(a.src);
    audioQueueRef.current = [];
    playingRef.current = false;
    setAudioLevel(0);
  }, [stopTTSAnalyser, setAudioLevel]);
  interruptAudioRef.current = interruptAudio;

  const queueSentenceTTS = useCallback((sentence: string) => {
    const clean = sentence.replace(/```[\s\S]*?```/g, 'code block omitted').replace(/[#*_~`>\[\]]/g, '').replace(/\n+/g, ' ').trim();
    if (!clean) return;
    synthesizeSpeech(clean).then((blob) => {
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioQueueRef.current.push(audio);
      if (!playingRef.current) playNextAudio();
    }).catch(() => {});
  }, [playNextAudio]);

  // ── Voice ──
  const handleVoiceTranscribed = useCallback((text: string) => {
    const clean = text?.trim();
    if (clean && clean.length > 1) { setInput(clean); pendingVoiceRef.current = true; }
  }, []);

  const { state: speechState, error: speechError, available: speechAvailable, startRecording, stopRecording, startBargeInMonitor, stopBargeInMonitor } = useSpeech({
    onTranscribed: handleVoiceTranscribed,
  });
  startRecordingRef.current = startRecording;
  stopBargeInRef.current = stopBargeInMonitor;
  startBargeInRef.current = startBargeInMonitor;

  // Show speech errors as toasts
  useEffect(() => {
    if (speechError) toast.error(speechError);
  }, [speechError]);

  // ── Always-on voice ──
  const sendMessageRef = useRef<(text?: string) => Promise<void>>(undefined);
  // Will be set after sendMessage is defined — for now set in an effect below

  const alwaysOnEnabled = speechEnabled && voiceAlwaysOn;

  const handleAlwaysOnTranscribed = useCallback((text: string) => {
    if (text && sendMessageRef.current) {
      voiceInitiatedRef.current = true;
      sendMessageRef.current(text);
    }
  }, []);

  const handleAlwaysOnBargeIn = useCallback(() => {
    interruptAudio();
  }, [interruptAudio]);

  const handleAlwaysOnAudioLevel = useCallback((rms: number) => {
    // Only update from mic input when Jarvis isn't speaking (TTS analyser handles that)
    if (!playingRef.current) setAudioLevel(rms);
  }, [setAudioLevel]);

  const handleAlwaysOnStateChange = useCallback((s: 'idle' | 'monitoring' | 'capturing' | 'transcribing') => {
    if (playingRef.current) return; // don't override 'speaking' state
    // Don't override 'thinking' state while model is generating
    if (useAppStore.getState().streamState.isStreaming) return;
    if (s === 'monitoring') setJarvisState('listening');
    else if (s === 'capturing') setJarvisState('listening');
    else if (s === 'transcribing') setJarvisState('thinking');
    else setJarvisState('idle');
  }, [setJarvisState]);

  useAlwaysOnVoice({
    enabled: alwaysOnEnabled,
    jarvisSpeaking: jarvisState === 'speaking',
    onTranscribed: handleAlwaysOnTranscribed,
    onBargeIn: handleAlwaysOnBargeIn,
    onAudioLevel: handleAlwaysOnAudioLevel,
    onStateChange: handleAlwaysOnStateChange,
  });

  const handleMicClick = useCallback(async () => {
    if (!speechAvailable) {
      toast.error('Speech backend not available — check server config');
      return;
    }
    interruptAudio();
    if (speechState === 'recording') {
      try {
        const text = await stopRecording();
        if (text && text.trim()) { setInput(text); pendingVoiceRef.current = true; }
      } catch {
        toast.error('Transcription failed');
      }
    } else {
      voiceInitiatedRef.current = true;
      await startRecording();
    }
  }, [speechState, speechAvailable, startRecording, stopRecording, interruptAudio]);

  // ── Streaming ──
  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    resetStream();
  }, [resetStream]);

  const sendMessage = useCallback(async (overrideText?: string) => {
    const content = (overrideText || input).trim();
    if (!content || streamState.isStreaming) return;
    if (!selectedModel) { toast.error('Pick a model first (⌘K)'); return; }

    setInput('');
    sentenceBufferRef.current = '';
    audioQueueRef.current = [];

    let convId = activeId;
    if (!convId) convId = createConversation(selectedModel);

    const userMsg: ChatMessage = { id: generateId(), role: 'user', content, timestamp: Date.now() };
    addMessage(convId, userMsg);

    const currentMessages = useAppStore.getState().messages;
    const apiMessages = currentMessages.map((m) => ({ role: m.role, content: m.content }));

    const assistantMsg: ChatMessage = { id: generateId(), role: 'assistant', content: '', timestamp: Date.now() };
    addMessage(convId, assistantMsg);

    const startTime = Date.now();
    const timer = setInterval(() => setStreamState({ elapsedMs: Date.now() - startTime }), 100);
    timerRef.current = timer;
    const controller = new AbortController();
    abortRef.current = controller;

    let accumulatedContent = '';
    let usage: TokenUsage | undefined;
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined;
    const toolCalls: ToolCallInfo[] = [];
    let lastFlush = 0;
    let ttftMs: number | undefined;

    setStreamState({ isStreaming: true, phase: 'Generating...', elapsedMs: 0, activeToolCalls: [], content: '' });
    setJarvisState('thinking');

    try {
      for await (const sseEvent of streamChat(
        { model: selectedModel, messages: apiMessages, stream: true, temperature, max_tokens: maxTokens },
        controller.signal,
      )) {
        const eventName = sseEvent.event;
        if (eventName === 'agent_turn_start') {
          setStreamState({ phase: 'Agent thinking...' });
        } else if (eventName === 'inference_start') {
          setStreamState({ phase: 'Generating...' });
        } else if (eventName === 'tool_call_start') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc: ToolCallInfo = { id: generateId(), tool: data.tool, arguments: data.arguments || '', status: 'running' };
            toolCalls.push(tc);
            setStreamState({ phase: `Calling ${data.tool}...`, activeToolCalls: [...toolCalls] });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
          } catch {}
        } else if (eventName === 'tool_call_end') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc = toolCalls.find((t) => t.tool === data.tool && t.status === 'running');
            if (tc) { tc.status = data.success ? 'success' : 'error'; tc.latency = data.latency; tc.result = data.result; }
            setStreamState({ phase: 'Generating...', activeToolCalls: [...toolCalls] });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
          } catch {}
        } else {
          try {
            const data = JSON.parse(sseEvent.data);
            const delta = data.choices?.[0]?.delta;
            if (data.usage) usage = data.usage;
            if (data.complexity) complexity = data.complexity;
            if (delta?.content) {
              if (!ttftMs) ttftMs = Date.now() - startTime;
              accumulatedContent += delta.content;
              setStreamState({ content: accumulatedContent, phase: '' });

              if (speechEnabled) {
                sentenceBufferRef.current += delta.content;
                const sentenceMatch = sentenceBufferRef.current.match(/^([\s\S]*?[.!?])\s+([\s\S]*)$/);
                if (sentenceMatch) {
                  const completeSentence = sentenceMatch[1].trim();
                  sentenceBufferRef.current = sentenceMatch[2];
                  if (completeSentence) queueSentenceTTS(completeSentence);
                }
              }

              const now = Date.now();
              if (now - lastFlush >= 80) {
                updateLastAssistant(convId, accumulatedContent, toolCalls.length > 0 ? [...toolCalls] : undefined);
                lastFlush = now;
              }
            }
            if (data.choices?.[0]?.finish_reason === 'stop') break;
          } catch {}
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        if (!accumulatedContent) accumulatedContent = '(Generation stopped)';
      } else {
        accumulatedContent = accumulatedContent || `Error: ${err?.message || String(err)}`;
      }
    } finally {
      if (!accumulatedContent) accumulatedContent = 'No response was generated. Please try again.';
      const totalMs = Date.now() - startTime;
      const _CLOUD_PREFIXES = ['gpt-', 'o1-', 'o3-', 'o4-', 'claude-', 'gemini-', 'openrouter/', 'MiniMax-', 'chatgpt-'];
      const engineLabel = _CLOUD_PREFIXES.some(p => selectedModel.startsWith(p)) ? 'cloud' : 'ollama';
      const telemetry: MessageTelemetry = {
        engine: engineLabel, model_id: selectedModel, total_ms: totalMs, ttft_ms: ttftMs,
        tokens_per_sec: usage?.completion_tokens ? usage.completion_tokens / (totalMs / 1000) : undefined,
        complexity_score: complexity?.score, complexity_tier: complexity?.tier, suggested_max_tokens: complexity?.suggested_max_tokens,
      };
      updateLastAssistant(convId, accumulatedContent, toolCalls.length > 0 ? toolCalls : undefined, usage, telemetry);
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
      resetStream();
      abortRef.current = null;
      if (speechEnabled && sentenceBufferRef.current.trim()) { queueSentenceTTS(sentenceBufferRef.current); sentenceBufferRef.current = ''; }
      // Only go idle if TTS isn't playing — playNextAudio handles the
      // final state transition when all audio finishes.
      if (!playingRef.current) setJarvisState('idle');
      voiceInitiatedRef.current = false;
      fetchSavings().then((data) => useAppStore.getState().setSavings(data)).catch(() => {});
    }
  }, [input, activeId, selectedModel, streamState.isStreaming, createConversation, addMessage, updateLastAssistant, setStreamState, resetStream, temperature, maxTokens, speechEnabled, voiceAlwaysOn, queueSentenceTTS, setJarvisState]);

  // Keep sendMessageRef current for always-on voice callback
  sendMessageRef.current = sendMessage;

  // Auto-send after voice transcription (manual mic mode)
  useEffect(() => {
    if (pendingVoiceRef.current && input.trim()) {
      pendingVoiceRef.current = false;
      voiceInitiatedRef.current = true;
      sendMessage();
    }
  }, [input, sendMessage]);

  // Track last response so we can show a brief snippet on the ball
  useEffect(() => {
    if (!streamState.isStreaming && messages.length > 0) {
      const last = messages[messages.length - 1];
      if (last?.role === 'assistant' && last.content) {
        lastResponseRef.current = last.content;
      }
    }
  }, [messages, streamState.isStreaming]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  // ── Derived state ──
  const runningAgents = agents.filter((a) => a.status === 'running');
  const voiceStatusLabel = alwaysOnEnabled
    ? jarvisState === 'speaking' ? 'SPEAKING'
    : jarvisState === 'listening' ? 'LISTENING'
    : jarvisState === 'thinking' ? 'PROCESSING'
    : null
    : null;

  const statusLabel = voiceStatusLabel || (isStreaming ? 'GENERATING' : runningAgents.length > 0 ? 'AGENTS ACTIVE' : 'STANDING BY');
  const statusSub = isStreaming
    ? streamState.phase || 'streaming'
    : alwaysOnEnabled
    ? `always-on · ${runningAgents.length > 0 ? `${runningAgents.length} running` : 'idle'} · ${agents.length} agents`
    : `${runningAgents.length > 0 ? `${runningAgents.length} running` : 'idle'} · memory warm · ${agents.length} agents scheduled`;
  const modelLabel = selectedModel || serverInfo?.model || 'no model';
  const engineLabel = serverInfo?.engine || 'local';

  // Telemetry data
  const latencyMs = streamState.isStreaming ? streamState.elapsedMs : 0;

  return (
    <div className="h-full overflow-y-auto relative" style={{ background: '#030305', color: '#eef0f4', fontFamily: "'Geist Variable', 'Geist', system-ui, sans-serif" }}>
      {/* Atmosphere */}
      <div className="pt-atmosphere" />

      {/* Frame */}
      <div className="pt-frame">
        {/* Top bar */}
        <div className="pt-topbar">
          <span className="pt-heartbeat" />
          <span className="pt-hud">openjarvis // {engineLabel} // {modelLabel}</span>
          <button
            className="pt-hud pt-hud-dim"
            style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer' }}
            onClick={() => setCommandPaletteOpen(true)}
          >
            ⌘K command
          </button>
        </div>

        {/* Grid */}
        <div className="pt-grid">
          {/* ── Core Stage — always hero, conversations save to /chat ── */}
          <div className={`pt-panel pt-core-stage pt-active ${isStreaming ? 'pt-streaming' : ''}`}>
            <JarvisOrb
              state={jarvisState}
              audioLevel={audioLevel}
              alwaysOnActive={alwaysOnEnabled}
            />
            <h1 className="pt-display pt-aberrate" data-text={statusLabel}>{statusLabel}</h1>
            <div className="pt-hud pt-statusline">{statusSub}</div>

            {/* Input bar — always visible */}
            <div className="pt-inputbar" onClick={() => inputRef.current?.focus()}>
              {/* Always-on toggle */}
              <button
                className={`pt-always-on ${alwaysOnEnabled ? 'pt-active' : ''}`}
                onClick={(e) => { e.stopPropagation(); updateSettings({ voiceAlwaysOn: !voiceAlwaysOn }); }}
                disabled={!speechEnabled}
                title={alwaysOnEnabled ? 'Disable always-on listening' : 'Enable always-on listening'}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M4 4C2.9 5.1 2.2 6.5 2.2 8s.7 2.9 1.8 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                  <path d="M12 4c1.1 1.1 1.8 2.5 1.8 4s-.7 2.9-1.8 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                </svg>
              </button>
              {/* Manual mic button — hidden when always-on is active */}
              {!alwaysOnEnabled && (
                <button
                  className={`pt-mic ${speechState === 'recording' ? 'pt-recording' : ''}`}
                  onClick={(e) => { e.stopPropagation(); handleMicClick(); }}
                  disabled={!speechEnabled || !speechAvailable || streamState.isStreaming}
                  title={!speechAvailable ? 'Speech backend unavailable' : speechState === 'recording' ? 'Stop recording' : 'Start recording'}
                >
                  ●
                </button>
              )}
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={selectedModel ? 'Speak or type a command...' : 'Select a model first (⌘K)...'}
                disabled={streamState.isStreaming}
              />
              {streamState.isStreaming ? (
                <button className="pt-stop" onClick={stopStreaming} title="Stop generating">
                  <Square size={12} />
                </button>
              ) : (
                <>
                  {input.trim() ? (
                    <button
                      className="pt-send"
                      onClick={() => sendMessage()}
                      disabled={!input.trim() || !selectedModel}
                      title="Send"
                    >
                      <Send size={16} />
                    </button>
                  ) : (
                    <span className="pt-wave" aria-hidden="true">
                      <span /><span /><span /><span /><span />
                    </span>
                  )}
                </>
              )}
            </div>
          </div>

          {/* ── Rail ── */}
          <div className="pt-rail">
            {/* Daily Briefing */}
            {briefing.status !== 'idle' && (
              <div className="pt-panel pt-card">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                  <h3 style={{ marginBottom: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                    Daily Briefing
                    {briefing.status === 'speaking' && (
                      <span className="pt-hud" style={{ color: '#3df2dd', fontSize: 9, animation: 'pt-beat 1.5s ease-in-out infinite' }}>● LIVE</span>
                    )}
                  </h3>
                  <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    {briefing.status === 'speaking' ? (
                      <button className="pt-briefing-ctrl pt-stop-ctrl" onClick={interrupt}><VolumeX size={10} /> Stop</button>
                    ) : briefing.text && briefing.status === 'ready' ? (
                      <button className="pt-briefing-ctrl" onClick={playBriefing} style={{ color: '#3df2dd' }}><Play size={10} /> Play</button>
                    ) : null}
                    <button
                      className="pt-briefing-ctrl"
                      onClick={refreshBriefing}
                      disabled={briefing.status === 'loading' || briefing.status === 'generating'}
                      style={{ color: '#5c5e68' }}
                    >
                      <RefreshCw size={10} className={briefing.status === 'loading' || briefing.status === 'generating' ? 'animate-spin' : ''} />
                    </button>
                  </div>
                </div>
                {(briefing.status === 'loading' || briefing.status === 'generating') && !briefing.text && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center', padding: '12px 0' }}>
                    <Loader2 size={12} className="animate-spin" style={{ color: '#3df2dd' }} />
                    <span className="pt-hud" style={{ fontSize: 11 }}>{briefing.status === 'generating' ? 'Preparing briefing...' : 'Loading...'}</span>
                  </div>
                )}
                {briefing.text && (
                  <div style={{ position: 'relative' }}>
                    {briefing.status === 'speaking' && <div className="pt-briefing-live-bar" />}
                    <p className="pt-briefing-body" style={{ paddingLeft: briefing.status === 'speaking' ? 10 : 0 }}>
                      {briefing.text}
                    </p>
                  </div>
                )}
                {briefing.followUpQuestions && briefing.followUpQuestions.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
                    <span className="pt-hud" style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#5c5e68' }}>Quick check</span>
                    {briefing.followUpQuestions.map((q, i) => (
                      <button
                        key={i}
                        className="pt-followup-chip"
                        onClick={() => { setInput(q); inputRef.current?.focus(); }}
                        style={{
                          background: 'rgba(61, 242, 221, 0.06)',
                          border: '1px solid rgba(61, 242, 221, 0.15)',
                          borderRadius: 6,
                          padding: '6px 10px',
                          color: '#a8aab4',
                          fontSize: 11,
                          lineHeight: 1.4,
                          textAlign: 'left',
                          cursor: 'pointer',
                          transition: 'all 150ms ease',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(61, 242, 221, 0.12)'; e.currentTarget.style.color = '#eef0f4'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(61, 242, 221, 0.06)'; e.currentTarget.style.color = '#a8aab4'; }}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                )}
                {briefing.status === 'error' && (
                  <div className="pt-hud" style={{ textAlign: 'center', padding: '8px 0', fontSize: 11 }}>
                    {briefing.error || 'Could not load briefing.'}
                    <button onClick={refreshBriefing} style={{ marginLeft: 6, color: '#3df2dd', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }}>
                      Retry
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Approval pending */}
            {approvals.length > 0 && (
              <div className="pt-panel pt-card pt-attention">
                <h3>Approval pending</h3>
                {approvals.slice(0, 3).map((action) => (
                  <div key={action.id}>
                    <div style={{ fontSize: '13.5px', color: '#a8aab4', lineHeight: 1.55 }}>
                      <b style={{ color: '#eef0f4', fontWeight: 500 }}>{action.action_type}</b>{' '}
                      {action.description}
                    </div>
                    <div className="pt-btnrow">
                      <button
                        className="pt-ghost pt-approve"
                        onClick={() => handleApprove(action.id)}
                        disabled={!!processing[action.id]}
                      >
                        APPROVE
                      </button>
                      <button
                        className="pt-ghost"
                        onClick={() => handleDeny(action.id)}
                        disabled={!!processing[action.id]}
                      >
                        DENY
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Agents */}
            <div className="pt-panel pt-card">
              <h3>Agents</h3>
              {agents.length === 0 ? (
                <div className="pt-hud pt-hud-dim" style={{ textAlign: 'center', padding: '12px 0' }}>No agents configured</div>
              ) : (
                agents.map((agent) => (
                  <div className="pt-row" key={agent.id}>
                    <span className={`pt-dot ${agentDotClass(agent.status)}`} />
                    {agent.name}
                    <span className="pt-meta pt-hud pt-hud-dim">{agentLabel(agent.status)}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Telemetry footer */}
        <div className="pt-panel pt-telemetry">
          <span className="pt-hud">latency <b>{latencyMs ? `${latencyMs}ms` : '—'}</b></span>
          <span className="pt-hud">{engineLabel} <b>{modelLabel}</b></span>
          {approvals.length > 0 && (
            <span className="pt-hud" style={{ marginLeft: 'auto', color: '#f5a524' }}>
              {approvals.length} approval{approvals.length > 1 ? 's' : ''} pending
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
