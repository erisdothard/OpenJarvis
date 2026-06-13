import { useState, useRef, useCallback, useEffect } from 'react';
import { Send, Square, Paperclip, Search, X } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore, generateId } from '../../lib/store';
import { streamChat, streamResearch } from '../../lib/sse';
import { apiFetch, fetchSavings, getBase, synthesizeSpeech } from '../../lib/api';
import { listConnectors, getSyncStatus } from '../../lib/connectors-api';
import { MicButton } from './MicButton';
import { useVoiceStream } from '../../hooks/useVoiceStream';
import { useAudioManager } from '../../hooks/useAudioManager';
import type { AudioManagerReturn } from '../../hooks/useAudioManager';
import { extractSentence, cleanForTTS } from '../../lib/sentenceBuffer';
import type {
  ChatMessage,
  MessageTelemetry,
  ResearchSearchTrace,
  ResearchSource,
  TokenUsage,
  ToolCallInfo,
} from '../../types';

// While Deep Research is toggled on, poll connected sources for sync
// progress so we can surface "Searching over N items — sync in progress"
// next to the toggle. Polling is gated on `enabled` so toggling DR off
// stops the network chatter immediately.
function useResearchCorpusSync(enabled: boolean): {
  syncing: boolean;
  itemsSynced: number;
} {
  const [state, setState] = useState({ syncing: false, itemsSynced: 0 });

  useEffect(() => {
    if (!enabled) {
      setState({ syncing: false, itemsSynced: 0 });
      return;
    }
    let cancelled = false;

    const poll = async () => {
      try {
        const list = await listConnectors();
        const connected = list.filter((c) => c.connected);
        if (connected.length === 0) {
          if (!cancelled) setState({ syncing: false, itemsSynced: 0 });
          return;
        }
        const results = await Promise.all(
          connected.map(async (c) => {
            try {
              return await getSyncStatus(c.connector_id);
            } catch {
              return null;
            }
          }),
        );
        let syncing = false;
        let itemsSynced = 0;
        for (const r of results) {
          if (!r) continue;
          if (r.state === 'syncing') syncing = true;
          itemsSynced += r.items_synced ?? 0;
        }
        if (!cancelled) setState({ syncing, itemsSynced });
      } catch {
        // Network blip — leave previous state intact.
      }
    };

    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [enabled]);

  return state;
}

export function InputArea() {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activeId = useAppStore((s) => s.activeId);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const streamState = useAppStore((s) => s.streamState);
  const messages = useAppStore((s) => s.messages);
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const maxTokens = useAppStore((s) => s.settings.maxTokens);
  const temperature = useAppStore((s) => s.settings.temperature);
  const createConversation = useAppStore((s) => s.createConversation);
  const addMessage = useAppStore((s) => s.addMessage);
  const updateLastAssistant = useAppStore((s) => s.updateLastAssistant);
  const setStreamState = useAppStore((s) => s.setStreamState);
  const resetStream = useAppStore((s) => s.resetStream);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const deepResearch = useAppStore((s) => s.deepResearch);
  const setDeepResearch = useAppStore((s) => s.setDeepResearch);
  const corpusSync = useResearchCorpusSync(deepResearch);

  const pendingVoiceRef = useRef(false);
  const voiceInitiatedRef = useRef(false);
  const sentenceBufferRef = useRef('');

  // Ref to break circular dependency between audioManager and voiceStream
  const audioManagerRef = useRef<AudioManagerReturn>(null!);

  // ── Audio manager (single source of truth for all playback) ──
  const audioManager = useAudioManager({
    onPlaybackFinished: () => {
      voiceInitiatedRef.current = false;
    },
  });
  audioManagerRef.current = audioManager;

  // ── WebSocket voice stream ──
  const voiceStream = useVoiceStream({
    onTranscript: useCallback((text: string, isFinal: boolean) => {
      if (!isFinal) return;
      const clean = text?.trim();
      if (clean && clean.length > 1) {
        setInput(clean);
        pendingVoiceRef.current = true;
      }
    }, []),
    onAudioData: useCallback((pcm: ArrayBuffer) => {
      audioManagerRef.current.enqueuePCM(pcm);
    }, []),
    onStopPlayback: useCallback(() => {
      audioManagerRef.current.interruptAll();
    }, []),
    onError: useCallback((detail: string) => {
      toast.error(detail);
    }, []),
  });

  const wsVoiceActive = voiceStream.state !== 'disconnected';

  // File attachments
  const [attachedFiles, setAttachedFiles] = useState<{ name: string; chunks: number }[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    e.target.value = ''; // reset so same file can be re-selected

    setUploading(true);
    try {
      const formData = new FormData();
      for (const f of Array.from(files)) {
        formData.append('files[]', f);
      }
      const res = await apiFetch('/v1/connectors/upload/ingest/files', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const err = await res.text().catch(() => res.statusText);
        throw new Error(err);
      }
      const data = await res.json();
      const newFiles = Array.from(files).map((f) => ({
        name: f.name,
        chunks: Math.ceil((data.chunks_added || 0) / files.length),
      }));
      setAttachedFiles((prev) => [...prev, ...newFiles]);
      toast.success(`Indexed ${data.chunks_added} chunks from ${files.length} file${files.length > 1 ? 's' : ''}`);
    } catch (err: any) {
      toast.error(`Upload failed: ${err?.message || 'unknown error'}`);
    } finally {
      setUploading(false);
    }
  }, []);

  const removeFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // (audio queue + useSpeech replaced by useAudioManager + useVoiceStream above)

  // Abort in-flight stream when the user switches models mid-generation.
  // This prevents errors from trying to continue a stream with a stale model.
  // Backend-initiated fallback switches set this ref to skip the abort.
  const prevModelRef = useRef(selectedModel);
  const fallbackSwitchRef = useRef(false);
  useEffect(() => {
    if (prevModelRef.current !== selectedModel && streamState.isStreaming) {
      if (fallbackSwitchRef.current) {
        // Backend fallback — don't abort, just acknowledge the new model
        fallbackSwitchRef.current = false;
      } else {
        // User-initiated switch — abort the stream
        abortRef.current?.abort();
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
        resetStream();
        abortRef.current = null;
      }
    }
    prevModelRef.current = selectedModel;
  }, [selectedModel, streamState.isStreaming, resetStream]);

  const micDisabled = !speechEnabled || voiceStream.available === false || streamState.isStreaming;
  const micReason: 'not-enabled' | 'no-backend' | 'streaming' | undefined =
    !speechEnabled ? 'not-enabled'
    : voiceStream.available === false ? 'no-backend'
    : streamState.isStreaming ? 'streaming'
    : undefined;

  const handleMicClick = useCallback(async () => {
    if (voiceStream.available === false) {
      toast.error('Voice backend not available — check server config');
      return;
    }
    if (wsVoiceActive) {
      voiceStream.disconnect();
    } else {
      audioManager.interruptAll();
      voiceInitiatedRef.current = true;
      await voiceStream.connect({ model: selectedModel }).catch(() => {
        toast.error('Voice connection failed');
      });
    }
  }, [voiceStream, wsVoiceActive, selectedModel, audioManager]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [input]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    resetStream();
  }, [resetStream]);

  const sendMessage = useCallback(async () => {
    const content = input.trim();
    if (!content || streamState.isStreaming) return;
    if (!selectedModel) {
      toast.error('Pick a model first (⌘K)');
      return;
    }

    setInput('');
    sentenceBufferRef.current = '';

    let convId = activeId;
    if (!convId) {
      convId = createConversation(selectedModel);
    }

    // If files were attached, prepend context so the LLM uses knowledge_search
    let finalContent = content;
    if (attachedFiles.length > 0) {
      const names = attachedFiles.map((f) => f.name).join(', ');
      finalContent = `[Attached files: ${names}] Use the knowledge_search tool to find their contents.\n\n${content}`;
    }

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: finalContent,
      timestamp: Date.now(),
    };
    addMessage(convId, userMsg);
    setAttachedFiles([]);

    // Build API messages before adding assistant placeholder
    const currentMessages = useAppStore.getState().messages;
    const apiMessages: { role: string; content: string }[] = [];

    // Inject briefing context so the model knows what the daily briefing contained
    const briefingText = useAppStore.getState().briefing.text;
    if (briefingText) {
      apiMessages.push({
        role: 'system',
        content: `[Daily Briefing Context]\nThe user was shown the following daily briefing at the start of this session. They may reference it or ask to modify it.\n\n${briefingText}`,
      });
    }

    apiMessages.push(
      ...currentMessages.map((m) => ({
        role: m.role,
        content: m.content,
      })),
    );

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isResearch: deepResearch || undefined,
    };
    addMessage(convId, assistantMsg);

    // Start streaming
    const startTime = Date.now();
    const timer = setInterval(() => {
      setStreamState({ elapsedMs: Date.now() - startTime });
    }, 100);
    timerRef.current = timer;

    const controller = new AbortController();
    abortRef.current = controller;

    let accumulatedContent = '';
    let usage: TokenUsage | undefined;
    let complexity: { score: number; tier: string; suggested_max_tokens: number } | undefined;
    let actualModel = selectedModel; // tracks the real model (may change on provider fallback)
    const toolCalls: ToolCallInfo[] = [];
    const researchTraces: ResearchSearchTrace[] = [];
    const researchSourcesByRef = new Map<number, ResearchSource>();
    const flushSources = () =>
      Array.from(researchSourcesByRef.values()).sort((a, b) => a.ref - b.ref);
    let lastFlush = 0;
    let ttftMs: number | undefined;

    const ttsGen = audioManager.getGeneration();

    setStreamState({
      isStreaming: true,
      phase: deepResearch ? 'Researching...' : 'Generating...',
      elapsedMs: 0,
      activeToolCalls: [],
      content: '',
    });
    useAppStore.getState().addLogEntry({
      timestamp: Date.now(),
      level: 'info',
      category: 'chat',
      message: deepResearch
        ? `Research: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}"`
        : `Request: "${content.slice(0, 80)}${content.length > 80 ? '...' : ''}" → ${selectedModel}`,
    });

    try {
      if (deepResearch) {
        for await (const ev of streamResearch(content, controller.signal)) {
          if (ev.type === 'search_call') {
            const trace: ResearchSearchTrace = {
              id: generateId(),
              query: ev.arguments?.query ?? '',
              person: ev.arguments?.person,
              timeRange: ev.arguments?.time_range,
              status: 'pending',
            };
            researchTraces.push(trace);
            setStreamState({ phase: `Searching: ${trace.query}` });
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: 'info',
              category: 'tool',
              message: `Search: "${trace.query}"${trace.person ? ` (person: ${trace.person})` : ''}`,
            });
          } else if (ev.type === 'search_result') {
            const pending = [...researchTraces].reverse().find((t) => t.status === 'pending');
            if (pending) {
              pending.status = 'complete';
              pending.numHits = ev.num_hits;
              pending.topTitles = ev.top_titles;
            }
            if (ev.sources) {
              for (const src of ev.sources) {
                if (src && typeof src.ref === 'number' && !researchSourcesByRef.has(src.ref)) {
                  researchSourcesByRef.set(src.ref, src);
                }
              }
            }
            updateLastAssistant(
              convId,
              accumulatedContent,
              undefined,
              undefined,
              undefined,
              undefined,
              [...researchTraces],
              flushSources(),
            );
          } else if (ev.type === 'synthesis') {
            if (!ttftMs) ttftMs = Date.now() - startTime;
            accumulatedContent += ev.text;
            setStreamState({ content: accumulatedContent, phase: '' });
            const now = Date.now();
            if (now - lastFlush >= 80) {
              updateLastAssistant(
                convId,
                accumulatedContent,
                undefined,
                undefined,
                undefined,
                undefined,
                [...researchTraces],
                flushSources(),
              );
              lastFlush = now;
            }
          } else if (ev.type === 'system_metrics') {
            // Live GPU sample — feed straight to the System panel so Power
            // (W) and Energy (kJ) tick up in real time as the agent runs.
            useAppStore.getState().setLiveEnergy({
              power_w: ev.power_w,
              energy_j: ev.energy_j,
              duration_s: ev.duration_s,
            });
          } else if (ev.type === 'error') {
            // Backend setup/worker failure (Ollama down, planner model
            // missing, KnowledgeStore locked, etc.). Without surfacing the
            // message, the user sees only the generic "No response was
            // generated" fallback and has no way to self-diagnose.
            const msg = ev.message || 'Research failed (no detail provided)';
            accumulatedContent = accumulatedContent
              ? `${accumulatedContent}\n\n**Research stopped:** ${msg}`
              : `**Research failed:** ${msg}`;
            setStreamState({ content: accumulatedContent, phase: '' });
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(),
              level: 'error',
              category: 'chat',
              message: `Deep Research error: ${msg}`,
            });
            toast.error(msg, { duration: 8000 });
          } else if (ev.type === 'done') {
            if (ev.usage) {
              usage = {
                prompt_tokens: ev.usage.prompt_tokens ?? 0,
                completion_tokens: ev.usage.completion_tokens ?? 0,
                total_tokens:
                  ev.usage.total_tokens ??
                  (ev.usage.prompt_tokens ?? 0) +
                    (ev.usage.completion_tokens ?? 0),
              };
              // Optimistically roll this research turn into the session
              // counters so the Session panel updates the moment the
              // stream finishes, regardless of how /v1/savings aggregates
              // research telemetry server-side.
              useAppStore.getState().incrementSavings(usage);
            }
            // Hold the final live numbers visible for a beat so the panel
            // doesn't flash to 0 between the SSE close and the next
            // /v1/telemetry/energy poll picking up the persisted record.
            window.setTimeout(() => {
              useAppStore.getState().setLiveEnergy(null);
            }, 1500);
            break;
          }
        }
      } else {
      for await (const sseEvent of streamChat(
        { model: selectedModel, messages: apiMessages, stream: true, temperature, max_tokens: maxTokens },
        controller.signal,
      )) {
        const eventName = sseEvent.event;

        if (eventName === 'agent_turn_start') {
          setStreamState({ phase: 'Agent thinking...' });
        } else if (eventName === 'inference_start') {
          setStreamState({ phase: 'Generating...' });
          useAppStore.getState().addLogEntry({
            timestamp: Date.now(), level: 'info', category: 'chat',
            message: `Generating with ${selectedModel}...`,
          });
        } else if (eventName === 'tool_call_start') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc: ToolCallInfo = {
              id: generateId(),
              tool: data.tool,
              arguments: data.arguments || '',
              status: 'running',
            };
            toolCalls.push(tc);
            setStreamState({
              phase: `Calling ${data.tool}...`,
              activeToolCalls: [...toolCalls],
            });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
            useAppStore.getState().addLogEntry({
              timestamp: Date.now(), level: 'info', category: 'tool',
              message: `Calling ${data.tool}(${data.arguments || ''})`,
            });
          } catch {}
        } else if (eventName === 'tool_call_end') {
          try {
            const data = JSON.parse(sseEvent.data);
            const tc = toolCalls.find(
              (t) => t.tool === data.tool && t.status === 'running',
            );
            if (tc) {
              tc.status = data.success ? 'success' : 'error';
              tc.latency = data.latency;
              tc.result = data.result;
            }
            setStreamState({
              phase: 'Generating...',
              activeToolCalls: [...toolCalls],
            });
            updateLastAssistant(convId, accumulatedContent, [...toolCalls]);
          } catch {}
        } else {
          try {
            const data = JSON.parse(sseEvent.data);
            const delta = data.choices?.[0]?.delta;
            if (data.usage) usage = data.usage;
            if (data.complexity) complexity = data.complexity;
            // Detect provider fallback — backend switches model on billing errors
            if (data.model && data.model !== actualModel) {
              actualModel = data.model;
              fallbackSwitchRef.current = true;
              useAppStore.getState().setSelectedModel(actualModel);
            }
            if (delta?.content) {
              if (!ttftMs) ttftMs = Date.now() - startTime;
              accumulatedContent += delta.content;
              setStreamState({ content: accumulatedContent, phase: '' });

              // Sentence-level TTS (only when WS voice is NOT active)
              if (speechEnabled) {
                sentenceBufferRef.current += delta.content;
                const result = extractSentence(sentenceBufferRef.current);
                if (result) {
                  sentenceBufferRef.current = result.remainder;
                  const clean = cleanForTTS(result.sentence);
                  if (clean) {
                    synthesizeSpeech(clean).then((blob) => {
                      audioManager.enqueueBlob(URL.createObjectURL(blob), ttsGen);
                    }).catch(() => {});
                  }
                }
              }

              const now = Date.now();
              if (now - lastFlush >= 80) {
                updateLastAssistant(
                  convId,
                  accumulatedContent,
                  toolCalls.length > 0 ? [...toolCalls] : undefined,
                );
                lastFlush = now;
              }
            }
            if (data.choices?.[0]?.finish_reason === 'stop') break;
          } catch {}
        }
      }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        // User cancelled or model switch — keep whatever was accumulated
        if (!accumulatedContent) accumulatedContent = '(Generation stopped)';
      } else {
        const errMsg = err?.message || String(err);
        accumulatedContent =
          accumulatedContent || `Error: ${errMsg}`;
        useAppStore.getState().addLogEntry({
          timestamp: Date.now(), level: 'error', category: 'chat',
          message: `Stream error: ${errMsg}`,
        });
      }
      // If we tore out mid-research, make sure the live System panel
      // numbers don't get stuck on the last sample.
      useAppStore.getState().setLiveEnergy(null);
    } finally {
      if (!accumulatedContent) {
        accumulatedContent = 'No response was generated. Please try again.';
      }
      const totalMs = Date.now() - startTime;
      const _CLOUD_PREFIXES = ['gpt-', 'o1-', 'o3-', 'o4-', 'claude-', 'gemini-', 'openrouter/', 'MiniMax-', 'chatgpt-', 'groq/'];
      const engineLabel = _CLOUD_PREFIXES.some(p => actualModel.startsWith(p)) ? 'cloud' : 'ollama';
      const telemetry: MessageTelemetry = {
        engine: engineLabel,
        model_id: actualModel,
        total_ms: totalMs,
        ttft_ms: ttftMs,
        tokens_per_sec: usage?.completion_tokens
          ? usage.completion_tokens / (totalMs / 1000)
          : undefined,
        complexity_score: complexity?.score,
        complexity_tier: complexity?.tier,
        suggested_max_tokens: complexity?.suggested_max_tokens,
      };
      updateLastAssistant(
        convId,
        accumulatedContent,
        toolCalls.length > 0 ? toolCalls : undefined,
        usage,
        telemetry,
        undefined,
        researchTraces.length > 0 ? researchTraces : undefined,
        researchSourcesByRef.size > 0 ? flushSources() : undefined,
      );
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      resetStream();
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(), level: 'info', category: 'chat',
        message: `Response: ${accumulatedContent.length} chars`,
      });
      abortRef.current = null;

      // Flush remaining sentence buffer for TTS
      if (speechEnabled && sentenceBufferRef.current.trim()) {
        const clean = cleanForTTS(sentenceBufferRef.current);
        if (clean) {
          synthesizeSpeech(clean).then((blob) => {
            audioManager.enqueueBlob(URL.createObjectURL(blob), ttsGen);
          }).catch(() => {});
        }
        sentenceBufferRef.current = '';
      }
      voiceInitiatedRef.current = false;

      // Research path updates session counters optimistically from the
      // `done` event's usage payload — re-fetching here would overwrite
      // it with a potentially stale snapshot if the server's research
      // telemetry hasn't been merged into /v1/savings yet.
      if (!deepResearch) {
        fetchSavings()
          .then((data) => useAppStore.getState().setSavings(data))
          .catch(() => {});
      }
    }
  }, [
    input,
    activeId,
    selectedModel,
    streamState.isStreaming,
    createConversation,
    addMessage,
    updateLastAssistant,
    setStreamState,
    resetStream,
    deepResearch,
    temperature,
    maxTokens,
    speechEnabled,
    wsVoiceActive,
    audioManager,
  ]);

  // Auto-send after voice transcription fills the input
  useEffect(() => {
    if (pendingVoiceRef.current && input.trim()) {
      pendingVoiceRef.current = false;
      voiceInitiatedRef.current = true;
      sendMessage();
    }
  }, [input, sendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="px-3 sm:px-4 pb-4 pt-2" style={{ maxWidth: 'var(--chat-max-width)', margin: '0 auto', width: '100%', paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))' }}>
      {/* Deep Research toggle */}
      <div className="mb-2 flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setDeepResearch(!deepResearch)}
            disabled={streamState.isStreaming}
            aria-pressed={deepResearch}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[12px] transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50 rounded-full ${deepResearch ? 'hud-tag' : ''}`}
            style={!deepResearch ? {
              background: 'transparent',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-tertiary)',
            } : {}}
            title={deepResearch ? 'Deep Research: on' : 'Deep Research: off'}
          >
            <Search size={12} />
            Deep Research
          </button>
        </div>
        {deepResearch && corpusSync.syncing && corpusSync.itemsSynced > 0 && (
          <div className="text-[12px] leading-snug" style={{ color: 'var(--color-text-tertiary)' }}>
            Indexing{' '}
            <span key={corpusSync.itemsSynced} className="sync-bump" style={{ color: 'var(--color-accent)' }}>
              {corpusSync.itemsSynced.toLocaleString()}
            </span>{' '}
            items — sync in progress
          </div>
        )}
      </div>

      {/* Attached files */}
      {attachedFiles.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {attachedFiles.map((f, i) => (
            <span
              key={`${f.name}-${i}`}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-[12px] rounded-lg"
              style={{
                background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid rgba(255, 255, 255, 0.08)',
                color: 'var(--color-text-secondary)',
              }}
            >
              <Paperclip size={11} style={{ opacity: 0.5 }} />
              {f.name}
              <button
                type="button"
                onClick={() => removeFile(i)}
                className="ml-0.5 p-0.5 rounded hover:bg-white/10 transition-colors cursor-pointer"
                title="Remove file"
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".txt,.md,.pdf,.docx,.csv,.json,.py,.ts,.tsx,.js,.jsx"
        onChange={handleFileSelect}
        className="hidden"
      />

      {/* Input bar */}
      <div
        className="chroma-border flex items-center gap-2 px-4 py-3 transition-shadow rounded-2xl"
        style={{
          background: 'rgba(255, 255, 255, 0.015)',
          border: '1px solid rgba(255, 255, 255, 0.04)',
        }}
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={selectedModel ? 'Message Jarvis...' : 'Select a model first (⌘K)...'}
          rows={1}
          className="flex-1 bg-transparent outline-none resize-none text-[16px] leading-relaxed"
          style={{
            color: 'var(--color-text-bright)',
            maxHeight: '200px',
          }}
          disabled={streamState.isStreaming || modelLoading}
        />
        {streamState.isStreaming ? (
          <button
            onClick={stopStreaming}
            className="p-3 transition-colors shrink-0 cursor-pointer rounded-xl"
            style={{
              background: 'var(--color-error)',
              color: '#fff',
              minWidth: 44,
              minHeight: 44,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title="Stop generating"
          >
            <Square size={16} />
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title="Attach files"
              className="p-2.5 shrink-0 cursor-pointer rounded-xl transition-colors hover:bg-white/5 disabled:opacity-30 disabled:cursor-default"
              style={{ color: 'var(--color-text-tertiary)', minWidth: 44, minHeight: 44, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <Paperclip size={16} />
            </button>
            <MicButton
              state={voiceStream.state}
              onClick={handleMicClick}
              disabled={micDisabled}
              reason={micReason}
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || modelLoading || !selectedModel}
              title={selectedModel ? 'Send' : 'Select a model first (⌘K)'}
              className="chroma-button-primary p-2.5 shrink-0 cursor-pointer disabled:opacity-30 disabled:cursor-default rounded-xl"
              style={{ minWidth: 44, minHeight: 44, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <Send size={16} />
            </button>
          </div>
        )}
      </div>

      {/* Hint — hidden on mobile (touch users don't need keyboard shortcuts) */}
      <div className="hidden sm:flex items-center justify-center mt-2 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>
          <kbd className="font-mono text-[10px]">Enter</kbd> to send &middot;{' '}
          <kbd className="font-mono text-[10px]">Shift+Enter</kbd> new line
        </span>
      </div>
    </div>
  );
}
