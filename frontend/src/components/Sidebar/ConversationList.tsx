import { Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router';
import { useAppStore } from '../../lib/store';

interface Props {
  searchQuery: string;
  onNavigate?: () => void;
}

function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return new Date(timestamp).toLocaleDateString();
}

export function ConversationList({ searchQuery, onNavigate }: Props) {
  const navigate = useNavigate();
  const conversations = useAppStore((s) => s.conversations);
  const activeId = useAppStore((s) => s.activeId);
  const selectConversation = useAppStore((s) => s.selectConversation);
  const deleteConversation = useAppStore((s) => s.deleteConversation);
  const setSidebarOpen = useAppStore((s) => s.setSidebarOpen);

  const filtered = searchQuery
    ? conversations.filter((c) =>
        c.title.toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : conversations;

  if (filtered.length === 0) {
    return (
      <div className="px-3 py-8 text-center text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
        {searchQuery ? 'No matches' : 'No conversations yet'}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 py-1">
      {filtered.map((conv) => {
        const isActive = conv.id === activeId;
        return (
          <div
            key={conv.id}
            className="group flex items-center cursor-pointer rounded-lg transition-colors"
            style={{
              background: isActive ? 'rgba(0, 229, 255, 0.05)' : 'transparent',
            }}
            onMouseEnter={(e) => {
              if (!isActive) e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)';
            }}
            onMouseLeave={(e) => {
              if (!isActive) e.currentTarget.style.background = 'transparent';
            }}
          >
            <button
              onClick={() => {
                selectConversation(conv.id);
                navigate('/chat');
                if (onNavigate) onNavigate();
                else if (window.innerWidth < 768) setSidebarOpen(false);
              }}
              className="flex-1 text-left px-3 py-2.5 min-w-0 cursor-pointer"
            >
              <div className="text-[13px] truncate" style={{
                color: isActive ? 'var(--color-text-bright)' : 'var(--color-text-secondary)',
                fontWeight: isActive ? 500 : 400,
              }}>
                {conv.title}
              </div>
              <div className="text-[11px] mt-0.5" style={{ color: 'var(--color-text-tertiary)' }}>
                {formatRelativeTime(conv.updatedAt)}
              </div>
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                deleteConversation(conv.id);
              }}
              className="p-1.5 mr-1.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity cursor-pointer rounded-md"
              style={{ color: 'var(--color-text-tertiary)' }}
              onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-error)')}
              onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-tertiary)')}
              title="Delete"
            >
              <Trash2 size={13} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
