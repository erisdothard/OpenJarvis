import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router';
import {
  Database,
  Bot,
  ScrollText,
  Rocket,
  ChevronDown,
  Loader2,
  Search,
} from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { ConversationList } from '../Sidebar/ConversationList';

const SECONDARY_NAV = [
  { path: '/data-sources', icon: Database, label: 'Data Sources' },
  { path: '/agents', icon: Bot, label: 'Agents' },
  { path: '/logs', icon: ScrollText, label: 'Logs' },
  { path: '/get-started', icon: Rocket, label: 'Get Started' },
];

export function MoreSheet() {
  const navigate = useNavigate();
  const location = useLocation();
  const moreSheetOpen = useAppStore((s) => s.moreSheetOpen);
  const setMoreSheetOpen = useAppStore((s) => s.setMoreSheetOpen);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const deepResearch = useAppStore((s) => s.deepResearch);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen);

  const [searchQuery, setSearchQuery] = useState('');

  const displayModel = deepResearch
    ? 'Deep Research'
    : selectedModel || serverInfo?.model || 'Select model';

  const handleNav = (path: string) => {
    setMoreSheetOpen(false);
    navigate(path);
  };

  const handleClose = () => setMoreSheetOpen(false);

  return (
    <div
      className={`more-sheet ${moreSheetOpen ? 'is-open' : ''}`}
      aria-hidden={!moreSheetOpen}
    >
      {/* Drag handle */}
      <div className="more-sheet-handle" />

      {/* Model selector */}
      <div className="more-sheet-section">
        <button
          onClick={() => {
            setCommandPaletteOpen(true);
            setMoreSheetOpen(false);
          }}
          className="model-badge more-sheet-model"
        >
          {modelLoading ? (
            <Loader2 size={14} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
          ) : (
            <div className="w-2 h-2 rounded-full shrink-0" style={{
              background: deepResearch ? 'var(--color-accent)' : 'var(--color-success)',
            }} />
          )}
          <span className="flex-1 text-left truncate text-[13px]" style={{
            color: deepResearch ? 'var(--color-accent)' : 'var(--color-text)',
          }}>
            {displayModel}
          </span>
          <ChevronDown size={12} style={{ color: 'var(--color-text-tertiary)' }} />
        </button>
      </div>

      {/* Secondary navigation */}
      <div className="more-sheet-section">
        <span className="more-sheet-label">Navigate</span>
        <div className="more-sheet-nav">
          {SECONDARY_NAV.map((item) => {
            const isActive = location.pathname === item.path;
            return (
              <button
                key={item.path}
                onClick={() => handleNav(item.path)}
                className={`more-sheet-nav-item ${isActive ? 'is-active' : ''}`}
              >
                <item.icon size={18} strokeWidth={1.5} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Conversations */}
      <div className="more-sheet-section more-sheet-conversations">
        <span className="more-sheet-label">Conversations</span>
        <div className="more-sheet-search">
          <Search size={14} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
          <input
            type="text"
            placeholder="Search chats..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 bg-transparent outline-none text-[13px]"
            style={{ color: 'var(--color-text)' }}
          />
        </div>
        <div className="more-sheet-convo-list">
          <ConversationList searchQuery={searchQuery} onNavigate={handleClose} />
        </div>
      </div>
    </div>
  );
}
