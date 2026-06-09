import { useState, useCallback, useRef } from 'react';
import { getBase } from '../lib/api';

export type LiveKitState = 'disconnected' | 'connecting' | 'connected';

interface LiveKitToken {
  token: string;
  url: string;
  room: string;
  identity: string;
}

/**
 * Fetch a LiveKit access token from the backend and check availability.
 */
export function useLiveKit() {
  const [state, setState] = useState<LiveKitState>('disconnected');
  const [available, setAvailable] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef<LiveKitToken | null>(null);

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${getBase()}/v1/livekit/health`);
      const data = await res.json();
      setAvailable(data.available);
      return data.available as boolean;
    } catch {
      setAvailable(false);
      return false;
    }
  }, []);

  const fetchToken = useCallback(async (room = 'jarvis'): Promise<LiveKitToken | null> => {
    setError(null);
    setState('connecting');
    try {
      const res = await fetch(`${getBase()}/v1/livekit/token?room=${encodeURIComponent(room)}`);
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail);
      }
      const data: LiveKitToken = await res.json();
      tokenRef.current = data;
      setState('connected');
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to get LiveKit token';
      setError(msg);
      setState('disconnected');
      return null;
    }
  }, []);

  const disconnect = useCallback(() => {
    tokenRef.current = null;
    setState('disconnected');
    setError(null);
  }, []);

  return {
    state,
    available,
    error,
    token: tokenRef.current,
    checkHealth,
    fetchToken,
    disconnect,
  };
}
