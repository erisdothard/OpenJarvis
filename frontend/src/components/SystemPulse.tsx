import { useEffect, useState } from 'react';
import { useAppStore } from '../lib/store';
import { fetchManagedAgents } from '../lib/api';

type PulseState = 'idle' | 'inferencing' | 'agent-active' | 'hidden';

export function SystemPulse({ apiReachable }: { apiReachable: boolean | null }) {
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const [hasRunningAgent, setHasRunningAgent] = useState(false);

  useEffect(() => {
    if (apiReachable === false) return;
    const check = () =>
      fetchManagedAgents()
        .then((agents) => setHasRunningAgent(agents.some((a) => a.status === 'running')))
        .catch(() => {});
    check();
    const interval = setInterval(check, 30000);
    return () => clearInterval(interval);
  }, [apiReachable]);

  if (apiReachable === false) return null;

  let state: PulseState = 'idle';
  if (isStreaming) state = 'inferencing';
  if (hasRunningAgent) state = 'agent-active';

  const styles: Record<Exclude<PulseState, 'hidden'>, React.CSSProperties> = {
    idle: {
      background: 'linear-gradient(90deg, transparent 10%, rgba(0, 229, 255, 0.08) 50%, transparent 90%)',
      height: '1px',
    },
    inferencing: {
      background: 'linear-gradient(90deg, #ff0080, #ff6d00, #ffea00, #00ff87, #00e5ff, #8c00ff, #ff0080)',
      backgroundSize: '300% 100%',
      animation: 'chroma-travel 2s linear infinite',
      height: '2px',
    },
    'agent-active': {
      background: 'linear-gradient(90deg, #00e5ff, #00ff87, #ffea00, #ff6d00, #ff0080, #8c00ff, #00e5ff)',
      backgroundSize: '300% 100%',
      animation: 'chroma-travel 1.2s linear infinite',
      height: '2px',
    },
  };

  return (
    <div
      className="fixed top-0 left-0 right-0 z-50"
      style={styles[state]}
    />
  );
}
