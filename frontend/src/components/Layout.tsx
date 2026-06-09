import { useEffect, useState } from 'react';
import { Outlet, useNavigate } from 'react-router';
import { ApprovalBell } from './ApprovalBell';
import { Sidebar } from './Sidebar/Sidebar';
import { SystemPulse } from './SystemPulse';
import { useAppStore } from '../lib/store';
import { checkHealth } from '../lib/api';

export function Layout() {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);

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
    <div className="flex flex-col h-full w-full overflow-hidden relative" style={{ background: 'var(--color-bg)' }}>
      {/* Chromatic ambient glow — slowly shifting spectral light in the void */}
      <div className="chromatic-ambient-glow" />
      <SystemPulse apiReachable={apiReachable} />
      <ApprovalBell />

      {apiReachable === false && (
        <div className="flex items-center gap-3 px-4 py-2.5 text-sm shrink-0 border-b border-[var(--color-border)]"
          style={{ background: 'rgba(248, 113, 113, 0.04)' }}
        >
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: 'var(--color-error)' }} />
          <span className="text-[var(--color-text)] font-medium">Backend offline</span>
          <button
            onClick={() => navigate('/settings')}
            className="text-sm cursor-pointer ml-auto shrink-0 font-medium"
            style={{ color: 'var(--color-accent)' }}
          >
            Configure
          </button>
        </div>
      )}

      <div className="flex flex-1 min-h-0 relative z-10">
        <Sidebar />
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-20 bg-black/50 md:hidden backdrop-blur-sm"
            onClick={() => useAppStore.getState().setSidebarOpen(false)}
          />
        )}
        <main className="flex-1 flex flex-col min-w-0 h-full relative overflow-hidden">
          <div className="flex-1 flex flex-col min-w-0 min-h-0 relative">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
