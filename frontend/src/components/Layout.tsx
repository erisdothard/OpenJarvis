import { useEffect, useState } from 'react';
import { Outlet, useNavigate } from 'react-router';
import { ApprovalBell } from './ApprovalBell';
import { Sidebar } from './Sidebar/Sidebar';
import { BottomTabBar } from './MobileNav/BottomTabBar';
import { MoreSheet } from './MobileNav/MoreSheet';
import { SystemPulse } from './SystemPulse';
import { useAppStore } from '../lib/store';
import { useIsMobile } from '../hooks/useIsMobile';
import { checkHealth } from '../lib/api';

export function Layout() {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);
  const isMobile = useIsMobile();

  useEffect(() => {
    const check = () => checkHealth().then(setApiReachable);
    check();
    const interval = setInterval(check, 30000);
    const onFocus = () => check();
    window.addEventListener('focus', onFocus);
    return () => {
      clearInterval(interval);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  const navigate = useNavigate();

  return (
    <div className="flex flex-col h-full w-full overflow-hidden relative" style={{ background: '#030305' }}>
      {/* Platinum atmosphere — liquid chrome background */}
      <div className="pt-atmosphere" />
      <SystemPulse apiReachable={apiReachable} />
      <ApprovalBell />

      {apiReachable === false && (
        <div className="flex items-center gap-3 px-4 py-2.5 text-sm shrink-0 border-b border-[var(--color-border)]"
          style={{ background: 'rgba(248, 113, 113, 0.04)' }}
        >
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: 'var(--color-error)' }} />
          <span className="text-[var(--color-text)] font-medium text-xs sm:text-sm">Backend offline</span>
          <button
            onClick={() => navigate('/settings')}
            className="text-sm cursor-pointer ml-auto shrink-0 font-medium"
            style={{ color: 'var(--color-accent)', minHeight: 44, display: 'flex', alignItems: 'center' }}
          >
            Configure
          </button>
        </div>
      )}

      <div className="flex flex-1 min-h-0 relative z-10">
        {/* Desktop: sidebar. Mobile: hidden — bottom nav takes over */}
        {!isMobile && (
          <>
            <Sidebar />
            {sidebarOpen && (
              <div
                className="fixed inset-0 z-20 bg-black/50 md:hidden backdrop-blur-sm"
                onClick={() => useAppStore.getState().setSidebarOpen(false)}
              />
            )}
          </>
        )}
        <main className={`flex-1 flex flex-col min-w-0 h-full relative overflow-hidden ${isMobile ? 'pb-[72px]' : ''}`}>
          <div className="flex-1 flex flex-col min-w-0 min-h-0 relative">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Mobile: bottom nav + more sheet */}
      {isMobile && (
        <>
          <MoreSheet />
          <BottomTabBar />
        </>
      )}
    </div>
  );
}
