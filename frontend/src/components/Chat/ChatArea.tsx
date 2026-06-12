import { useRef, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { MessageBubble } from './MessageBubble';
import { InputArea } from './InputArea';
import { StreamingDots } from './StreamingDots';
import { VoiceAgent } from './VoiceAgent';
import { useAppStore } from '../../lib/store';
import { PanelRightOpen, PanelRightClose, Database, X, Sparkles, Mail, Search as SearchIcon, Bot } from 'lucide-react';
import { listConnectors } from '../../lib/connectors-api';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return 'Working late?';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

const SUGGESTIONS = [
  { icon: Mail, label: 'Check my email', action: 'Check my email and summarize what needs attention' },
  { icon: SearchIcon, label: 'Research a topic', action: 'Help me research ' },
  { icon: Bot, label: 'Run an agent', action: '' },
  { icon: Sparkles, label: 'Write something', action: 'Help me write ' },
];

export function ChatArea() {
  const messages = useAppStore((s) => s.messages);
  const streamState = useAppStore((s) => s.streamState);
  const systemPanelOpen = useAppStore((s) => s.systemPanelOpen);
  const toggleSystemPanel = useAppStore((s) => s.toggleSystemPanel);
  const navigate = useNavigate();
  const listRef = useRef<HTMLDivElement>(null);
  const shouldAutoScroll = useRef(true);

  const [hasConnectedSources, setHasConnectedSources] = useState<boolean | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    listConnectors()
      .then((list) => setHasConnectedSources(list.some((c) => c.connected)))
      .catch(() => setHasConnectedSources(null));
  }, []);

  useEffect(() => {
    if (shouldAutoScroll.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, streamState.content]);

  const handleScroll = () => {
    if (!listRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    shouldAutoScroll.current = scrollHeight - scrollTop - clientHeight < 100;
  };

  const isEmpty = messages.length === 0 && !streamState.isStreaming;
  const PanelIcon = systemPanelOpen ? PanelRightClose : PanelRightOpen;

  return (
    <div className="flex flex-col h-full">
      {/* Top bar */}
      <div className="flex items-center justify-end px-4 py-2 shrink-0">
        <button
          onClick={toggleSystemPanel}
          className="sidebar-icon-btn p-2.5 cursor-pointer"
          title={`${systemPanelOpen ? 'Hide' : 'Show'} system panel`}
          style={{ minWidth: 44, minHeight: 44, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        >
          <PanelIcon size={16} />
        </button>
      </div>

      {/* Data sources banner */}
      {hasConnectedSources === false && !bannerDismissed && (
        <div className="mx-4 mb-3 flex items-center gap-3 px-4 py-2.5 text-sm shrink-0 rounded-xl"
          style={{
            background: 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <Database size={14} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />
          <span className="flex-1 text-[13px]" style={{ color: 'var(--color-text-secondary)' }}>
            Connect data sources for better results
          </span>
          <button
            onClick={() => navigate('/data-sources')}
            className="text-[13px] font-medium cursor-pointer px-3 py-1 rounded-lg transition-colors"
            style={{ color: 'var(--color-accent)', background: 'var(--color-accent-subtle)' }}
          >
            Connect
          </button>
          <button
            onClick={() => setBannerDismissed(true)}
            className="p-1 cursor-pointer rounded-md transition-colors"
            style={{ color: 'var(--color-text-tertiary)', background: 'transparent', border: 'none' }}
          >
            <X size={12} />
          </button>
        </div>
      )}

      <div ref={listRef} onScroll={handleScroll} className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full px-4">
            {/* Greeting */}
            <div className="w-10 h-10 rounded-2xl mb-5 flex items-center justify-center"
              style={{ background: 'rgba(0, 229, 255, 0.04)', border: '1px solid rgba(0, 229, 255, 0.06)' }}
            >
              <Sparkles size={18} style={{ color: 'var(--color-accent)' }} />
            </div>

            <h2 className="text-xl sm:text-2xl font-semibold mb-2 tracking-tight" style={{ color: 'var(--color-text-bright)' }}>
              {getGreeting()}
            </h2>
            <p className="text-[14px] sm:text-[15px] text-center max-w-md mb-6 sm:mb-8 px-4" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
              What would you like to work on?
            </p>

            {/* Suggestion chips */}
            <div className="grid grid-cols-2 gap-2 sm:gap-2.5 max-w-md w-full px-2 sm:px-0">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.label}
                  onClick={() => {
                    if (s.action) {
                      // Pre-fill could be done via store but for now just navigate
                    }
                    navigate(s.label === 'Run an agent' ? '/agents' : '/chat');
                  }}
                  className="chromatic-void-card flex items-center gap-3 px-4 py-3.5 text-left cursor-pointer"
                  style={{
                    borderRadius: '12px',
                    color: 'var(--color-text-secondary)',
                  }}
                >
                  <s.icon size={16} style={{ color: 'var(--color-accent)', opacity: 0.7, flexShrink: 0 }} />
                  <span className="text-[13px] font-medium">{s.label}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="max-w-[var(--chat-max-width)] mx-auto px-3 sm:px-4 py-4 sm:py-6">
            {messages.map((msg, i) => {
              const isLastAssistant = i === messages.length - 1 && msg.role === 'assistant';
              return (
                <MessageBubble
                  key={msg.id}
                  message={msg}
                  isLive={isLastAssistant && streamState.isStreaming}
                />
              );
            })}
            {(() => {
              if (!streamState.isStreaming || streamState.content !== '') return null;
              const last = messages[messages.length - 1];
              if (last?.role === 'assistant' && last.isResearch) return null;
              return (
                <div className="flex justify-start mb-4">
                  <StreamingDots phase={streamState.phase} />
                </div>
              );
            })()}
          </div>
        )}
      </div>
      <VoiceAgent />
      <InputArea />
    </div>
  );
}
