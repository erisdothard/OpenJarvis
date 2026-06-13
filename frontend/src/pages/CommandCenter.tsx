import { useCallback, useEffect, useRef, useState } from 'react';
import { Send, Square, Play, VolumeX, RefreshCw, Loader2, ChevronDown } from 'lucide-react';
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
import { useVoiceStream } from '../hooks/useVoiceStream';
import { useAudioManager } from '../hooks/useAudioManager';
import type { AudioManagerReturn } from '../hooks/useAudioManager';
import { extractSentence, cleanForTTS } from '../lib/sentenceBuffer';
import { JarvisOrb } from '../components/JarvisOrb/JarvisOrb';
import { SituationalPanel } from '../components/CommandCenter/SituationalPanel';
import { TelemetryStrip } from '../components/CommandCenter/TelemetryStrip';
import { SocialPanel } from '../components/CommandCenter/SocialPanel';
import type { ManagedAgent, PendingApproval } from '../lib/api';
import { useIsMobile } from '../hooks/useIsMobile';
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

  const isMobile = useIsMobile(640);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [input, setInput] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastResponseRef = useRef('');
  const sentenceBufferRef = useRef('');
  const voiceInitiatedRef = useRef(false);
  const pendingVoiceRef = useRef(false);

  // Refs to break the circular dependency between audioManager and voiceStream:
  // audioManager callbacks need voiceStream state, voiceStream callbacks need audioManager functions.
  const audioManagerRef = useRef<AudioManagerReturn>(null!);
  const wsActiveRef = useRef(false);

  // ── Audio manager (single source of truth for all playback) ──
  const audioManager = useAudioManager({
    onPlaybackStart: () => {
      if (!wsActiveRef.current) setJarvisState('speaking');
    },
    onPlaybackFinished: () => {
      if (!wsActiveRef.current && !useAppStore.getState().streamState.isStreaming) {
        setJarvisState('idle');
      }
    },
  });
  audioManagerRef.current = audioManager;

  // Push AudioManager output level to the store for orb reactivity
  useEffect(() => {
    if (audioManager.isPlaying) setAudioLevel(audioManager.outputLevel);
  }, [audioManager.outputLevel, audioManager.isPlaying, setAudioLevel]);

  // ── WebSocket voice stream ──
  const voiceConvRef = useRef<string | null>(null);
  const voiceContentRef = useRef('');
  const voiceToolsRef = useRef<ToolCallInfo[]>([]);
  const wsAutoRef = useRef(false);

  const voiceStream = useVoiceStream({
    onTranscript: useCallback((text: string, isFinal: boolean) => {
      if (!isFinal || !text?.trim()) return;
      const model = useAppStore.getState().selectedModel;
      if (!model) return;
      const store = useAppStore.getState();
      const convId = store.activeId || store.createConversation(model);
      voiceConvRef.current = convId;
      voiceContentRef.current = '';
      voiceToolsRef.current = [];
      store.addMessage(convId, { id: generateId(), role: 'user', content: text.trim(), timestamp: Date.now() });
      store.addMessage(convId, { id: generateId(), role: 'assistant', content: '', timestamp: Date.now() });
      store.setStreamState({ isStreaming: true, phase: 'Generating...', elapsedMs: 0, activeToolCalls: [], content: '' });
    }, []),
    onLlmDelta: useCallback((delta: string) => {
      const convId = voiceConvRef.current;
      if (!convId) return;
      voiceContentRef.current += delta;
      const store = useAppStore.getState();
      store.setStreamState({ content: voiceContentRef.current, phase: '' });
      store.updateLastAssistant(convId, voiceContentRef.current, voiceToolsRef.current.length > 0 ? [...voiceToolsRef.current] : undefined);
    }, []),
    onLlmDone: useCallback((content: string) => {
      const convId = voiceConvRef.current;
      if (!convId) return;
      const final = content || voiceContentRef.current;
      const store = useAppStore.getState();
      store.updateLastAssistant(convId, final, voiceToolsRef.current.length > 0 ? voiceToolsRef.current : undefined);
      store.resetStream();
      voiceConvRef.current = null;
    }, []),
    onStateChange: useCallback((s: string) => {
      const store = useAppStore.getState();
      if (s === 'idle' || s === 'disconnected') store.setJarvisState('idle');
      else if (s === 'listening') store.setJarvisState('listening');
      else if (s === 'transcribing' || s === 'thinking') store.setJarvisState('thinking');
      else if (s === 'speaking') store.setJarvisState('speaking');
    }, []),
    onAudioLevel: useCallback((level: number) => {
      useAppStore.getState().setAudioLevel(level);
    }, []),
    onAudioData: useCallback((pcm: ArrayBuffer) => {
      audioManagerRef.current.enqueuePCM(pcm);
    }, []),
    onStopPlayback: useCallback(() => {
      audioManagerRef.current.interruptAll();
    }, []),
    onToolStart: useCallback((tool: string, args: string) => {
      const tc: ToolCallInfo = { id: generateId(), tool, arguments: args, status: 'running' };
      voiceToolsRef.current.push(tc);
      const convId = voiceConvRef.current;
      if (!convId) return;
      const store = useAppStore.getState();
      store.setStreamState({ phase: `Calling ${tool}...`, activeToolCalls: [...voiceToolsRef.current] });
      store.updateLastAssistant(convId, voiceContentRef.current, [...voiceToolsRef.current]);
    }, []),
    onToolEnd: useCallback((tool: string, success: boolean, latency: number) => {
      const tc = voiceToolsRef.current.find((t) => t.tool === tool && t.status === 'running');
      if (tc) { tc.status = success ? 'success' : 'error'; tc.latency = latency; }
      const convId = voiceConvRef.current;
      if (!convId) return;
      const store = useAppStore.getState();
      store.setStreamState({ phase: 'Generating...', activeToolCalls: [...voiceToolsRef.current] });
      store.updateLastAssistant(convId, voiceContentRef.current, [...voiceToolsRef.current]);
    }, []),
    onError: useCallback((detail: string) => {
      toast.error(detail);
    }, []),
  });

  const wsVoiceActive = voiceStream.state !== 'disconnected';
  wsActiveRef.current = wsVoiceActive;

  // Auto-connect WS voice when always-on is enabled and server supports it
  useEffect(() => {
    if (voiceStream.available && speechEnabled && voiceAlwaysOn && !wsVoiceActive) {
      wsAutoRef.current = true;
      voiceStream.connect({ model: selectedModel }).catch(() => { wsAutoRef.current = false; });
    }
    if ((!speechEnabled || !voiceAlwaysOn) && wsAutoRef.current && wsVoiceActive) {
      wsAutoRef.current = false;
      voiceStream.disconnect();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceStream.available, speechEnabled, voiceAlwaysOn, selectedModel]);

  // ── Briefing (uses shared AudioManager) ──
  const { interrupt: interruptBriefing, refresh: refreshBriefing, play: playBriefing } = useBriefing(audioManager);

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
    fetchSavings().then((data) => useAppStore.getState().setSavings(data)).catch(() => {});
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

  // ── Mic click ──
  const handleMicClick = useCallback(async () => {
    if (!voiceStream.available) {
      toast.error('Voice backend not available — server needs VAD + STT');
      return;
    }
    if (wsVoiceActive) {
      voiceStream.disconnect();
      wsAutoRef.current = false;
    } else {
      wsAutoRef.current = false;
      audioManager.interruptAll();
      await voiceStream.connect({ model: selectedModel }).catch(() => {
        toast.error('Voice connection failed');
      });
    }
  }, [voiceStream, wsVoiceActive, selectedModel, audioManager]);

  // ── Streaming (text input) ──
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

    const ttsGen = audioManager.getGeneration();

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

              // Sentence-level TTS for text-initiated chats
              if (speechEnabled) {
                sentenceBufferRef.current += delta.content;
                const result = extractSentence(sentenceBufferRef.current);
                if (result) {
                  sentenceBufferRef.current = result.remainder;
                  const clean = cleanForTTS(result.sentence);
                  if (clean) {
                    synthesizeSpeech(clean).then((blob) => {
                      audioManager.enqueueBlob(URL.createObjectURL(blob), ttsGen);
                    }).catch((err) => console.warn('[TTS] Stream sentence failed:', err?.message || err));
                  }
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

      if (speechEnabled && sentenceBufferRef.current.trim()) {
        const clean = cleanForTTS(sentenceBufferRef.current);
        if (clean) {
          synthesizeSpeech(clean).then((blob) => {
            audioManager.enqueueBlob(URL.createObjectURL(blob), ttsGen);
          }).catch((err) => console.warn('[TTS] Final sentence failed:', err?.message || err));
        }
        sentenceBufferRef.current = '';
      }

      if (!audioManager.isPlaying) setJarvisState('idle');
      voiceInitiatedRef.current = false;
      fetchSavings().then((data) => useAppStore.getState().setSavings(data)).catch(() => {});
    }
  }, [input, activeId, selectedModel, streamState.isStreaming, createConversation, addMessage, updateLastAssistant, setStreamState, resetStream, temperature, maxTokens, speechEnabled, wsVoiceActive, audioManager, setJarvisState]);

  useEffect(() => {
    if (pendingVoiceRef.current && input.trim()) {
      pendingVoiceRef.current = false;
      voiceInitiatedRef.current = true;
      sendMessage();
    }
  }, [input, sendMessage]);

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
  const alwaysOnUI = speechEnabled && voiceAlwaysOn;
  const alwaysOnFunctional = alwaysOnUI && voiceStream.available !== false;
  const voiceStatusLabel = alwaysOnUI
    ? jarvisState === 'speaking' ? 'SPEAKING'
    : jarvisState === 'listening' ? 'LISTENING'
    : jarvisState === 'thinking' ? 'PROCESSING'
    : null
    : null;

  const statusLabel = voiceStatusLabel || (isStreaming ? 'GENERATING' : runningAgents.length > 0 ? 'AGENTS ACTIVE' : 'STANDING BY');
  const statusSub = isStreaming
    ? streamState.phase || 'streaming'
    : alwaysOnUI
    ? `always-on · ${runningAgents.length > 0 ? `${runningAgents.length} running` : 'idle'} · ${agents.length} agents`
    : `${runningAgents.length > 0 ? `${runningAgents.length} running` : 'idle'} · memory warm · ${agents.length} agents scheduled`;
  const modelLabel = selectedModel || serverInfo?.model || 'no model';
  const engineLabel = serverInfo?.engine || 'local';
  const latencyMs = streamState.isStreaming ? streamState.elapsedMs : 0;

  return (
    <div className="h-full overflow-y-auto relative" style={{ background: '#030305', color: '#eef0f4', fontFamily: "'Geist Variable', 'Geist', system-ui, sans-serif" }}>
      <div className="pt-atmosphere" />
      <div className="pt-frame">
        <div className="pt-topbar">
          <span className="pt-heartbeat" />
          <span className="pt-hud">openjarvis // {engineLabel} // {modelLabel}</span>
          <button
            className="pt-hud pt-hud-dim"
            style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', minHeight: 44, display: 'flex', alignItems: 'center', padding: '0 8px' }}
            onClick={() => setCommandPaletteOpen(true)}
          >
            <span className="hidden sm:inline">⌘K command</span>
            <span className="sm:hidden">models</span>
          </button>
        </div>

        <div className="pt-grid">
          <div className={`pt-panel pt-core-stage pt-active ${isStreaming ? 'pt-streaming' : ''}`}>
            <JarvisOrb state={jarvisState} audioLevel={audioLevel} alwaysOnActive={alwaysOnFunctional} />
            <h1 className="pt-display pt-aberrate" data-text={statusLabel}>{statusLabel}</h1>
            <div className="pt-hud pt-statusline">{statusSub}</div>

            <div className="pt-inputbar" onClick={() => inputRef.current?.focus()}>
              <button
                className={`pt-always-on ${alwaysOnFunctional ? 'pt-active' : ''}`}
                onClick={(e) => { e.stopPropagation(); updateSettings({ voiceAlwaysOn: !voiceAlwaysOn }); }}
                disabled={!speechEnabled || voiceStream.available === false}
                title={alwaysOnFunctional ? 'Disable always-on listening' : voiceStream.available === false ? 'Voice backend not available' : 'Enable always-on listening'}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M4 4C2.9 5.1 2.2 6.5 2.2 8s.7 2.9 1.8 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                  <path d="M12 4c1.1 1.1 1.8 2.5 1.8 4s-.7 2.9-1.8 4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                </svg>
              </button>
              {!alwaysOnUI && (
                <button
                  className={`pt-mic ${wsVoiceActive ? 'pt-recording' : ''}`}
                  onClick={(e) => { e.stopPropagation(); handleMicClick(); }}
                  disabled={!speechEnabled || streamState.isStreaming}
                  title={wsVoiceActive ? 'Disconnect voice' : 'Start voice'}
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
                placeholder={selectedModel ? (isMobile ? 'Command...' : 'Speak or type a command...') : (isMobile ? 'Select model...' : 'Select a model first (⌘K)...')}
                disabled={streamState.isStreaming}
              />
              {streamState.isStreaming ? (
                <button className="pt-stop" onClick={stopStreaming} title="Stop generating">
                  <Square size={12} />
                </button>
              ) : (
                <>
                  {input.trim() ? (
                    <button className="pt-send" onClick={() => sendMessage()} disabled={!input.trim() || !selectedModel} title="Send">
                      <Send size={16} />
                    </button>
                  ) : (
                    <span className="pt-wave" aria-hidden="true"><span /><span /><span /><span /><span /></span>
                  )}
                </>
              )}
            </div>
          </div>

          <div className="pt-rail">
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
                      <button className="pt-briefing-ctrl pt-stop-ctrl" onClick={interruptBriefing}><VolumeX size={10} /> Stop</button>
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
                    <p className="pt-briefing-body" style={{ paddingLeft: briefing.status === 'speaking' ? 10 : 0 }}>{briefing.text}</p>
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
                        style={{ background: 'rgba(61, 242, 221, 0.06)', border: '1px solid rgba(61, 242, 221, 0.15)', borderRadius: 6, padding: '6px 10px', color: '#a8aab4', fontSize: 11, lineHeight: 1.4, textAlign: 'left', cursor: 'pointer', transition: 'all 150ms ease' }}
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
                    <button onClick={refreshBriefing} style={{ marginLeft: 6, color: '#3df2dd', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }}>Retry</button>
                  </div>
                )}
              </div>
            )}

            <SituationalPanel />

            {approvals.length > 0 && (
              <div className="pt-panel pt-card pt-attention">
                <h3>Approval pending</h3>
                {approvals.slice(0, 3).map((action) => (
                  <div key={action.id}>
                    <div style={{ fontSize: '13.5px', color: '#a8aab4', lineHeight: 1.55 }}>
                      <b style={{ color: '#eef0f4', fontWeight: 500 }}>{action.action_type}</b>{' '}{action.description}
                    </div>
                    <div className="pt-btnrow">
                      <button className="pt-ghost pt-approve" onClick={() => handleApprove(action.id)} disabled={!!processing[action.id]}>APPROVE</button>
                      <button className="pt-ghost" onClick={() => handleDeny(action.id)} disabled={!!processing[action.id]}>DENY</button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="pt-panel pt-card">
              <button className="pt-dropdown-toggle" onClick={() => setAgentsOpen((o) => !o)}>
                <h3 style={{ marginBottom: 0 }}>
                  Agents
                  {runningAgents.length > 0 && <span className="pt-hud" style={{ color: '#3df2dd', fontSize: 9, marginLeft: 8 }}>{runningAgents.length} running</span>}
                </h3>
                <ChevronDown size={14} className={`pt-chevron ${agentsOpen ? 'pt-chevron-open' : ''}`} />
              </button>
              {agentsOpen && (
                agents.length === 0
                  ? <div className="pt-hud pt-hud-dim" style={{ textAlign: 'center', padding: '12px 0' }}>No agents configured</div>
                  : agents.map((agent) => (
                      <div className="pt-row" key={agent.id}>
                        <span className={`pt-dot ${agentDotClass(agent.status)}`} />
                        {agent.name}
                        <span className="pt-meta pt-hud pt-hud-dim">{agentLabel(agent.status)}</span>
                      </div>
                    ))
              )}
            </div>

            <SocialPanel />
          </div>
        </div>

        <TelemetryStrip latencyMs={latencyMs} approvalsCount={approvals.length} />
      </div>
    </div>
  );
}
