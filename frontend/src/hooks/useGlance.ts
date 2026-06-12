import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchGlance } from '../lib/api';
import type { GlanceData } from '../lib/api';

const POLL_INTERVAL_MS = 60_000;

export function useGlance() {
  const [data, setData] = useState<GlanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    const result = await fetchGlance();
    if (result) setData(result);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refresh]);

  return { data, loading, refresh };
}
