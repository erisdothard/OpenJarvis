import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router';
import {
  MessageSquare,
  Plus,
  BarChart3,
  Settings,
  Search,
  PanelLeftClose,
  PanelLeft,
  Loader2,
  Bot,
  ScrollText,
  Database,
  LayoutDashboard,
  Rocket,
  ChevronDown,
} from 'lucide-react';
import { ConversationList } from './ConversationList';
import { useAppStore } from '../../lib/store';

export function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchQuery, setSearchQuery] = useState('');

  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const createConversation = useAppStore((s) => s.createConversation);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const deepResearch = useAppStore((s) => s.deepResearch);
  const messages = useAppStore((s) => s.messages);

  const setSidebarOpen = useAppStore((s) => s.setSidebarOpen);

  // Close sidebar on mobile after navigation
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;
  const closeSidebarOnMobile = () => {
    if (isMobile) setSidebarOpen(false);
  };

  const handleNewChat = () => {
    if (messages.length === 0) {
      navigate('/chat');
      closeSidebarOnMobile();
      return;
    }
    createConversation(selectedModel);
    navigate('/chat');
    closeSidebarOnMobile();
  };

  const navItems = [
    { path: '/', icon: LayoutDashboard, label: 'Home' },
    { path: '/chat', icon: MessageSquare, label: 'Chat' },
    { path: '/dashboard', icon: BarChart3, label: 'Dashboard' },
    { path: '/data-sources', icon: Database, label: 'Data Sources' },
    { path: '/agents', icon: Bot, label: 'Agents' },
    { path: '/logs', icon: ScrollText, label: 'Logs' },
    { path: '/settings', icon: Settings, label: 'Settings' },
    { path: '/get-started', icon: Rocket, label: 'Get Started' },
  ];

  const displayModel = deepResearch
    ? 'Deep Research'
    : selectedModel || serverInfo?.model || 'Select model';

  return (
    <>
      {!sidebarOpen && (
        <button
          onClick={toggleSidebar}
          className="fixed top-3 left-3 z-30 p-2.5 cursor-pointer rounded-lg transition-colors"
          style={{
            color: 'var(--color-text-secondary)',
            background: 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border)',
            minWidth: 44,
            minHeight: 44,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <PanelLeft size={18} />
        </button>
      )}

      <aside
        className={`flex flex-col h-full shrink-0 transition-all duration-200 ease-out overflow-hidden fixed md:relative z-30
          ${sidebarOpen ? 'w-[260px]' : 'w-0'}`}
        style={{
          background: 'rgba(0, 0, 0, 0.95)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          borderRight: sidebarOpen ? '1px solid rgba(255, 255, 255, 0.03)' : 'none',
        }}
      >
        <div className="flex flex-col h-full w-[260px]">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2.5"
            style={{ borderBottom: '1px solid var(--color-border-subtle)' }}
          >
            <button onClick={toggleSidebar} className="sidebar-icon-btn p-1.5 cursor-pointer" title="Collapse sidebar">
              <PanelLeftClose size={16} />
            </button>
            <button onClick={handleNewChat} className="sidebar-icon-btn p-1.5 cursor-pointer" title="New chat">
              <Plus size={16} />
            </button>
          </div>

          {/* Model selector */}
          <button
            onClick={() => setCommandPaletteOpen(true)}
            className="model-badge mx-3 mt-3 mb-2 flex items-center gap-2.5 px-3 py-2.5 text-sm transition-colors cursor-pointer"
            style={{
              background: 'rgba(255, 255, 255, 0.02)',
              color: 'var(--color-text)',
              border: '1px solid rgba(255, 255, 255, 0.03)',
              borderRadius: 'var(--radius-md)',
            }}
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

          {/* Search */}
          <div className="px-3 mb-2">
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg"
              style={{
                background: 'rgba(255, 255, 255, 0.015)',
                border: '1px solid rgba(255, 255, 255, 0.02)',
              }}
            >
              <Search size={13} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
              <input
                type="text"
                placeholder="Search chats..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="flex-1 bg-transparent outline-none text-[13px]"
                style={{ color: 'var(--color-text)' }}
              />
            </div>
          </div>

          {/* Conversations */}
          <div className="flex-1 overflow-y-auto px-2">
            <ConversationList searchQuery={searchQuery} />
          </div>

          {/* Navigation */}
          <nav className="px-2 pb-2 pt-2 flex flex-col gap-0.5"
            style={{ borderTop: '1px solid var(--color-border)' }}
          >
            {navItems.map((item) => {
              const isActive = location.pathname === item.path;
              return (
                <button
                  key={item.path}
                  onClick={() => { navigate(item.path); closeSidebarOnMobile(); }}
                  className={`sidebar-nav-item relative flex items-center gap-2.5 px-3 py-2.5 text-left w-full cursor-pointer ${isActive ? 'is-active' : ''}`}
                >
                  {isActive && <span aria-hidden="true" className="chroma-active-bar" />}
                  <item.icon size={15} style={{ color: isActive ? 'var(--color-accent)' : 'var(--color-text-secondary)' }} />
                  <span className="text-[13px]" style={{ fontWeight: isActive ? 500 : 400 }}>
                    {item.label}
                  </span>
                </button>
              );
            })}
          </nav>
        </div>
      </aside>
    </>
  );
}
