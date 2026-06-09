import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  ArrowRight,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  XCircle,
  MessageSquare,
  Mail,
  Play,
  Database,
  Inbox,
  Compass,
  Search,
  Sun,
  User,
  Volume2,
  VolumeX,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import { useAppStore } from '../lib/store';
import {
  fetchManagedAgents,
  fetchPendingApprovals,
  fetchServerInfo,
  approveAction,
  denyAction,
} from '../lib/api';
import { listConnectors } from '../lib/connectors-api';
import { useBriefing } from '../hooks/useBriefing';
import type { ManagedAgent, PendingApproval } from '../lib/api';
import type { ConnectorInfo } from '../types/connectors';

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

const TIER_COLORS: Record<string, string> = {
  trivial: 'var(--color-text-secondary)',
  low: '#00e5ff',
  medium: 'var(--color-warning)',
  high: 'var(--color-error)',
};

function agentStatus(agent: ManagedAgent) {
  const map: Record<string, { color: string; label: string }> = {
    idle: { color: '#6b7280', label: 'Idle' },
    running: { color: '#00ffcc', label: 'Running' },
    paused: { color: '#ffcc00', label: 'Paused' },
    error: { color: '#ff0055', label: 'Error' },
    needs_attention: { color: '#ffcc00', label: 'Attention' },
    stalled: { color: '#ffcc00', label: 'Stalled' },
    budget_exceeded: { color: '#ff0055', label: 'Budget' },
    archived: { color: '#4b5563', label: 'Archived' },
  };
  return map[agent.status] || map.idle;
}

const AGENT_ICONS = [Inbox, Compass, Search, Sun, User, Database, Mail, Play];

const ACTION_CARDS = [
  { title: 'Ask Jarvis', sub: 'Voice or text', id: 'XTS-001', category: 'COMPOSE', icon: MessageSquare, color: '#00e5ff', path: '/chat' },
  { title: 'Check Email', sub: 'Latest messages', id: 'MP.0884', category: 'INCOMING', icon: Mail, color: '#00ff87', path: '/chat' },
  { title: 'Run Agent', sub: '', id: 'T9Y.SUR', category: 'SYSTEM', icon: Play, color: '#ff6d00', path: '/agents' },
  { title: 'Data Sources', sub: '', id: '1.0-89592', category: 'PROCESS', icon: Database, color: '#8c00ff', path: '/data-sources' },
];

const TICKER_TEXT = '☆ Conceptual Artwork   ⚙ Systems Group   🌐 Metro Worldwide   ▮▮▮▮▮▮   ✵ Data Streams   ▮▮▮▮▮▮   ';

/* ── Main ── */

export function CommandCenter() {
  const navigate = useNavigate();
  const serverInfo = useAppStore((s) => s.serverInfo);
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const briefing = useAppStore((s) => s.briefing);

  const [agents, setAgents] = useState<ManagedAgent[]>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [processing, setProcessing] = useState<Record<string, boolean>>({});

  const { interrupt, refresh: refreshBriefing } = useBriefing();

  const refresh = useCallback(async () => {
    const [a, ap, c] = await Promise.allSettled([
      fetchManagedAgents(),
      fetchPendingApprovals(),
      listConnectors(),
    ]);
    if (a.status === 'fulfilled') setAgents(a.value);
    if (ap.status === 'fulfilled') setApprovals(ap.value);
    if (c.status === 'fulfilled') setConnectors(c.value);
  }, []);

  useEffect(() => {
    fetchServerInfo().then((info) => useAppStore.getState().setServerInfo(info)).catch(() => {});
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

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

  const runningAgents = agents.filter((a) => a.status === 'running');
  const connectedSources = connectors.filter((c) => c.connected);
  const statusLabel = isStreaming ? 'Generating' : runningAgents.length > 0 ? 'Agents running' : 'Idle';

  return (
    <div className="h-full overflow-y-auto font-mono text-xs text-[#E2E8F0]">
      <div className="flex flex-col p-5 gap-4 relative min-h-full">

        {/* ═══ HERO ═══ */}
        <section className="grid grid-cols-12 gap-4">

          {/* Left: Tech Spec Panel */}
          <div className="col-span-12 md:col-span-4 border border-[#1E293B] bg-[#0E131A]/80 p-4 rounded-2xl flex flex-col justify-between relative overflow-hidden">
            <div className="absolute top-0 right-0 px-2 py-1 text-[9px] text-gray-600 border-b border-l border-[#1E293B] rounded-bl-lg">TS28</div>
            <div>
              <div className="text-gray-500 text-[10px] tracking-tighter uppercase">Nanofuel System</div>
              <h2 className="text-base font-black tracking-wide uppercase mt-1 text-white leading-tight">
                Experimental<br />Vector<br />Simulation
              </h2>
              <div className="mt-3 text-gray-500 text-[10px] space-y-0.5">
                <div>SYS.TM // 2026_XTS263P708R1S2T9U4V5</div>
                <div>DEV // CRITICAL_CORE_INTERFACE</div>
              </div>
            </div>

            {/* Barcode + decoration */}
            <div className="mt-4 pt-4 border-t border-[#1E293B]/60 flex items-end justify-between">
              <div className="space-y-1">
                <div className="text-[9px] text-gray-500">SPECIAL EDITION [0 A]</div>
                <div className="w-28 h-7 relative opacity-40">
                  <div className="absolute inset-0" style={{ background: 'repeating-linear-gradient(90deg, #fff 0px, #fff 2px, transparent 2px, transparent 5px)' }} />
                </div>
              </div>
              <span className="text-xl text-cyan-400/60">▲</span>
            </div>
          </div>

          {/* Right: Chromatic Globe Hero Image */}
          <div
            className="col-span-12 md:col-span-8 rounded-2xl overflow-hidden relative h-56 md:h-64 shadow-2xl"
            style={{
              backgroundImage: 'url(/chroma-globe.jpg)',
              backgroundSize: 'cover',
              backgroundPosition: 'center',
            }}
          >
            {/* Bottom gradient for text legibility */}
            <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
            {/* Subtle chromatic shimmer overlay */}
            <div className="absolute inset-0 bg-gradient-to-br from-cyan-500/5 via-transparent to-purple-500/5" />

            {/* Bottom: Jarvis identity + status */}
            <div className="absolute bottom-0 left-0 right-0 p-5 flex justify-between items-end">
              <div>
                <h1 className="text-4xl font-extrabold text-white tracking-tight drop-shadow-lg" style={{ fontFamily: 'var(--font-display)' }}>
                  Jarvis
                </h1>
                <p className="text-[11px] text-gray-300 mt-1 flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: statusLabel === 'Idle' ? '#6b7280' : '#00ffcc' }} />
                  {statusLabel}
                  <span className="text-gray-500">·</span>
                  <span className="text-cyan-400">{serverInfo?.model || '—'}</span>
                  <span className="text-gray-500">·</span>
                  {agents.length} agents
                  <span className="text-gray-500">·</span>
                  {connectedSources.length} sources
                </p>
              </div>
              {/* Right barcode accent */}
              <div className="flex flex-col items-end">
                <div className="text-[8px] text-white/50 mb-1">CODE BAR // XTS.90</div>
                <div className="w-20 h-3 opacity-30" style={{ background: 'repeating-linear-gradient(90deg, #fff 0px, #fff 1px, transparent 1px, transparent 3px)' }} />
              </div>
            </div>
          </div>
        </section>

        {/* ═══ DAILY BRIEFING ═══ */}
        {briefing.status !== 'idle' && (
          <section className="g-chromatic-border rounded-2xl overflow-hidden bg-[#0E131A]/90 backdrop-blur-sm relative">
            {/* Speaking glow */}
            {briefing.status === 'speaking' && (
              <div className="absolute inset-0 rounded-2xl pointer-events-none" style={{ boxShadow: '0 0 20px rgba(0, 229, 255, 0.15), inset 0 0 20px rgba(0, 229, 255, 0.05)' }} />
            )}

            {/* Header */}
            <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-[#1E293B]/60">
              <div className="flex items-center gap-2">
                <Volume2 size={12} className="text-cyan-400" />
                <span className="text-[10px] font-semibold tracking-widest uppercase text-cyan-400/70">Daily Briefing</span>
                {briefing.status === 'speaking' && (
                  <span className="text-[9px] text-cyan-400/40 animate-pulse">● LIVE</span>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                {briefing.status === 'speaking' && (
                  <button
                    onClick={interrupt}
                    className="flex items-center gap-1 px-2 py-1 text-[10px] rounded-lg cursor-pointer transition-colors"
                    style={{ background: 'rgba(255,0,85,0.12)', color: '#ff0055', border: '1px solid rgba(255,0,85,0.15)' }}
                    title="Stop speaking"
                  >
                    <VolumeX size={10} />
                    Stop
                  </button>
                )}
                <button
                  onClick={refreshBriefing}
                  disabled={briefing.status === 'loading' || briefing.status === 'generating'}
                  className="p-1 rounded-lg cursor-pointer disabled:opacity-30 disabled:cursor-default transition-colors"
                  style={{ color: 'var(--color-text-tertiary)' }}
                  title="Refresh briefing"
                >
                  <RefreshCw size={11} className={briefing.status === 'loading' || briefing.status === 'generating' ? 'animate-spin' : ''} />
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="px-4 py-3">
              {(briefing.status === 'loading' || briefing.status === 'generating') && !briefing.text && (
                <div className="flex items-center gap-2 py-3 justify-center">
                  <Loader2 size={12} className="animate-spin text-cyan-400/60" />
                  <span className="text-[11px] text-gray-500">
                    {briefing.status === 'generating' ? 'Preparing your briefing...' : 'Loading briefing...'}
                  </span>
                </div>
              )}

              {briefing.text && (
                <div className="relative">
                  {briefing.status === 'speaking' && (
                    <div className="absolute -left-2.5 top-0 bottom-0 w-0.5 rounded-full animate-pulse" style={{ background: 'linear-gradient(to bottom, #00e5ff, #8c00ff)' }} />
                  )}
                  <p className="text-[12px] leading-relaxed text-gray-300 whitespace-pre-wrap">
                    {briefing.text}
                  </p>
                </div>
              )}

              {briefing.status === 'error' && (
                <div className="text-[11px] text-center py-2 text-gray-500">
                  {briefing.error || 'Could not load briefing.'}
                  <button onClick={refreshBriefing} className="ml-1.5 underline cursor-pointer text-cyan-400/60">Retry</button>
                </div>
              )}
            </div>
          </section>
        )}

        {/* ═══ QUICK ACTIONS ═══ */}
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {ACTION_CARDS.map((card, idx) => (
            <button
              key={idx}
              onClick={() => navigate(card.path)}
              className="g-chromatic-border rounded-2xl overflow-hidden bg-[#0E131A]/90 backdrop-blur-sm p-4 h-32 flex flex-col items-center justify-center gap-2 relative group hover:bg-[#141C29] transition cursor-pointer text-center"
            >
              {/* Corner labels */}
              <span className="absolute top-2 left-2.5 text-[8px] text-gray-500 uppercase tracking-wider">{card.category}</span>
              <span className="absolute top-2 right-2.5 text-[8px] text-yellow-500/70 font-mono">{card.id}</span>

              {/* Colored circle icon */}
              <div
                className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
                style={{ background: `${card.color}22`, border: `1.5px solid ${card.color}44` }}
              >
                <card.icon size={18} style={{ color: card.color }} />
              </div>

              {/* Label */}
              <div>
                <div className="text-white font-semibold text-sm tracking-tight">{card.title}</div>
                <div className="text-gray-400 text-[10px] mt-0.5">
                  {card.title === 'Run Agent'
                    ? `${agents.length} configured`
                    : card.title === 'Data Sources'
                      ? `${connectedSources.length} connected`
                      : card.sub}
                </div>
              </div>
            </button>
          ))}
        </section>

        {/* ═══ INFO GRID: AGENTS + PENDING ═══ */}
        <section className="grid grid-cols-12 gap-4 flex-1">

          {/* Left: Agents */}
          <div className="col-span-12 md:col-span-7 border border-[#1E293B] bg-[#0E131A]/90 rounded-2xl p-4 flex flex-col">
            <div className="flex justify-between items-center border-b border-[#1E293B] pb-2.5 mb-2">
              <span className="font-semibold text-sm text-gray-200">Agents</span>
              <button
                onClick={() => navigate('/agents')}
                className="text-cyan-400 text-[11px] hover:underline cursor-pointer flex items-center gap-1"
              >
                Manage <ArrowRight size={10} />
              </button>
            </div>

            {agents.length === 0 ? (
              <div className="flex-1 flex items-center justify-center text-gray-500 text-[11px]">
                No agents configured
              </div>
            ) : (
              <div className="flex-1 flex flex-col justify-center">
                {agents.map((agent, i) => {
                  const s = agentStatus(agent);
                  const Icon = AGENT_ICONS[i % AGENT_ICONS.length];
                  return (
                    <div
                      key={agent.id}
                      className="flex justify-between items-center py-2.5 text-gray-300 hover:bg-[#121924]/50 px-2 rounded-lg transition"
                    >
                      <div className="flex items-center gap-3">
                        <Icon size={14} className="text-gray-500" />
                        <span className="font-medium text-[13px]">{agent.name}</span>
                      </div>
                      <div className="flex items-center gap-3 text-[11px]">
                        <div className="flex items-center gap-1.5">
                          <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: s.color }} />
                          <span style={{ color: s.color }}>{s.label}</span>
                        </div>
                        {agent.last_run_at && (
                          <span className="font-mono text-gray-500 w-8 text-right">{timeAgo(agent.last_run_at)}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Right: Pending + System Stable */}
          <div className="col-span-12 md:col-span-5 flex flex-col gap-4">

            {/* Pending */}
            <div className="border border-[#1E293B] bg-[#0E131A]/90 rounded-2xl p-4 flex flex-col">
              <div className="flex justify-between items-center border-b border-[#1E293B] pb-2.5 mb-2">
                <span className="font-semibold text-sm text-gray-200">Pending</span>
                {approvals.length > 0 && (
                  <span className="text-[10px] text-yellow-500 font-mono">[{approvals.length}]</span>
                )}
              </div>

              {approvals.length === 0 ? (
                <div className="text-center py-3 text-gray-500 text-[12px] flex items-center justify-center gap-1.5">
                  <CheckCircle size={12} className="text-gray-600" /> All clear
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {approvals.slice(0, 3).map((action) => (
                    <ApprovalCard
                      key={action.id}
                      action={action}
                      loading={!!processing[action.id]}
                      onApprove={handleApprove}
                      onDeny={handleDeny}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* System Stable */}
            <div
              className="rounded-2xl overflow-hidden relative h-28 flex flex-col justify-center items-center"
              style={{
                backgroundImage: 'url(/chroma-7.jpg)',
                backgroundSize: 'cover',
                backgroundPosition: 'center',
              }}
            >
              <div className="absolute inset-0 bg-black/30" />
              <div className="z-10 text-center tracking-widest text-white font-black uppercase text-sm drop-shadow-[0_2px_8px_rgba(0,0,0,0.9)]">
                System Stable
              </div>
              <div className="absolute bottom-2 right-3 text-[8px] text-white/40 tracking-wider z-10">VER. XTS</div>
            </div>
          </div>
        </section>

        {/* ═══ BOTTOM: Chromatic Graffiti Text ═══ */}
        <section className="mt-2 flex items-center gap-4 overflow-hidden">
          <div className="flex items-center gap-3">
            <span className="text-2xl text-white/20">✦</span>
            <span className="text-2xl text-white/10">◆</span>
          </div>
          <h2
            className="chromatic-text-fill text-6xl md:text-7xl font-black uppercase tracking-tighter leading-none select-none whitespace-nowrap"
            style={{ fontFamily: 'var(--font-display)' }}
          >
            OPENJARVIS
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-2xl text-white/10">◆</span>
            <span className="text-2xl text-white/20">✦</span>
          </div>
        </section>

        {/* ═══ TICKER BAR ═══ */}
        <footer className="border-t border-[#1E293B] pt-2 cyber-ticker text-[10px] text-gray-500 tracking-wider">
          <div className="cyber-ticker-track">
            {TICKER_TEXT.repeat(4)}
          </div>
        </footer>

      </div>
    </div>
  );
}

/* ── Approval Card ── */

function ApprovalCard({ action, loading, onApprove, onDeny }: {
  action: PendingApproval; loading: boolean;
  onApprove: (id: string) => void; onDeny: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const tierColor = TIER_COLORS[action.tier] || TIER_COLORS.medium;
  const hasPayload = Object.keys(action.payload ?? {}).length > 0;

  return (
    <div className="p-2.5 rounded-xl border border-[#1E293B] bg-[#111823]/60 relative z-10">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] font-medium text-cyan-400">{action.action_type}</span>
        <span className="text-[10px] font-mono tracking-wide" style={{ color: tierColor }}>{action.tier}</span>
      </div>
      <p className="text-[11px] mb-1.5 leading-relaxed text-gray-400">{action.description}</p>
      {hasPayload && (
        <button className="flex items-center gap-1 text-[10px] mb-1.5 cursor-pointer text-gray-500" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ChevronUp size={9} /> : <ChevronDown size={9} />}
          {expanded ? 'Hide' : 'Details'}
        </button>
      )}
      {expanded && (
        <pre className="text-[10px] p-1.5 mb-1.5 overflow-x-auto rounded-lg bg-[#0A0D14] text-gray-500 font-mono" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
          {JSON.stringify(action.payload, null, 2)}
        </pre>
      )}
      <div className="flex gap-2">
        <button onClick={() => onApprove(action.id)} disabled={loading}
          className="flex-1 flex items-center justify-center gap-1 py-1 text-[11px] font-medium cursor-pointer disabled:opacity-40 rounded-lg transition-colors border border-[#1E293B] hover:bg-[#0E131A] text-green-400"
        >
          <CheckCircle size={10} /> Approve
        </button>
        <button onClick={() => onDeny(action.id)} disabled={loading}
          className="flex-1 flex items-center justify-center gap-1 py-1 text-[11px] font-medium cursor-pointer disabled:opacity-40 rounded-lg transition-colors border border-[#1E293B] hover:bg-[#0E131A] text-red-400"
        >
          <XCircle size={10} /> Deny
        </button>
      </div>
    </div>
  );
}
