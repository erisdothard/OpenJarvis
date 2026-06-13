import { useNavigate, useLocation } from 'react-router';
import {
  LayoutDashboard,
  MessageSquare,
  BarChart3,
  Settings,
  Menu,
} from 'lucide-react';
import { useAppStore } from '../../lib/store';

const PRIMARY_TABS = [
  { path: '/', icon: LayoutDashboard, label: 'Home' },
  { path: '/chat', icon: MessageSquare, label: 'Chat' },
  { path: '/dashboard', icon: BarChart3, label: 'Stats' },
  { path: '/settings', icon: Settings, label: 'Settings' },
  { path: '__more__', icon: Menu, label: 'More' },
] as const;

const PRIMARY_PATHS = new Set(PRIMARY_TABS.filter((t) => t.path !== '__more__').map((t) => t.path));

export function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();
  const moreSheetOpen = useAppStore((s) => s.moreSheetOpen);
  const setMoreSheetOpen = useAppStore((s) => s.setMoreSheetOpen);

  const handleTab = (path: string) => {
    if (path === '__more__') {
      setMoreSheetOpen(!moreSheetOpen);
      return;
    }
    setMoreSheetOpen(false);
    navigate(path);
  };

  return (
    <nav className="mobile-bottom-bar" aria-label="Main navigation">
      <div className="flex">
        {PRIMARY_TABS.map((tab) => {
          const isMore = tab.path === '__more__';
          const isActive = isMore
            ? moreSheetOpen
            : tab.path === location.pathname && !moreSheetOpen;
          // If on a secondary route (not in primary tabs), don't highlight any primary tab
          const onSecondary = !PRIMARY_PATHS.has(location.pathname as any) && !isMore;

          return (
            <button
              key={tab.path}
              className={`mobile-tab ${isActive && !onSecondary ? 'is-active' : ''}`}
              onClick={() => handleTab(tab.path)}
              aria-current={isActive ? 'page' : undefined}
            >
              <tab.icon size={20} strokeWidth={1.5} />
              <span className="mobile-tab-label">{tab.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
