import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { getBase } from './api';

/**
 * Global WebSocket subscription to all agent events.
 * Surfaces agent tick completions as toast notifications in the chat UI.
 */
export function useGlobalAgentNotifications(): void {
  const retryRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const buildUrl = (): string => {
      const base = getBase();
      let origin: string;
      if (base) {
        origin = base.replace(/^http/, 'ws');
      } else {
        const loc = window.location;
        origin = `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}`;
      }
      return `${origin}/v1/agents/events`;
    };

    const connect = () => {
      if (closed) return;
      try {
        ws = new WebSocket(buildUrl());
      } catch {
        schedule();
        return;
      }
      ws.onopen = () => {
        retryRef.current = 0;
      };
      ws.onmessage = (msg) => {
        try {
          const payload = JSON.parse(msg.data);
          if (payload.type === 'agent_tick_end' && payload.data?.status === 'ok') {
            const name = payload.data.agent_name || 'Agent';
            const summary = payload.data.result_summary || '';
            const preview = summary.length > 120 ? summary.slice(0, 120) + '…' : summary;
            toast.success(`${name} completed`, {
              description: preview || undefined,
              duration: 8000,
            });
          }
          if (payload.type === 'agent_tick_error') {
            const name = payload.data?.agent_name || 'Agent';
            toast.error(`${name} failed`, {
              description: payload.data?.error?.slice(0, 120) || 'Unknown error',
              duration: 8000,
            });
          }
        } catch {
          // ignore malformed
        }
      };
      ws.onclose = () => {
        if (!closed) schedule();
      };
      ws.onerror = () => {
        ws?.close();
      };
    };

    const schedule = () => {
      if (closed) return;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(retryRef.current, 5));
      retryRef.current += 1;
      reconnectTimer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);
}
