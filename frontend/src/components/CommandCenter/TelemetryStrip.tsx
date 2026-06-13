import { useEffect, useState } from 'react';
import { useAppStore } from '../../lib/store';
import { getMemoryStats } from '../../lib/api';
import { useGlance } from '../../hooks/useGlance';
import type { MemoryStats } from '../../lib/api';

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface Props {
  latencyMs: number;
  approvalsCount: number;
}

export function TelemetryStrip({ latencyMs, approvalsCount }: Props) {
  const savings = useAppStore((s) => s.savings);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const { data: glance } = useGlance();

  const [memStats, setMemStats] = useState<MemoryStats | null>(null);

  useEffect(() => {
    getMemoryStats().then(setMemStats).catch(() => {});
    const id = setInterval(() => {
      getMemoryStats().then(setMemStats).catch(() => {});
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  const modelLabel = selectedModel || serverInfo?.model || 'no model';
  const engineLabel = serverInfo?.engine || 'local';

  const readouts: Array<{ label: string; value: string }> = [];

  readouts.push({
    label: 'tokens',
    value: savings ? fmtNum(savings.total_tokens) : '—',
  });
  readouts.push({
    label: 'calls',
    value: savings ? String(savings.total_calls) : '—',
  });
  readouts.push({
    label: 'memory',
    value: memStats ? String(memStats.entries) : '—',
  });
  readouts.push({
    label: 'uptime',
    value: glance?.uptime?.display || '—',
  });
  readouts.push({
    label: engineLabel,
    value: modelLabel,
  });

  return (
    <div className="pt-panel pt-telemetry">
      {readouts.map((r) => (
        <span className="pt-readout" key={r.label}>
          <span className="pt-readout-value">{r.value}</span>
          <span className="pt-readout-label">{r.label}</span>
        </span>
      ))}
      {latencyMs > 0 && (
        <span className="pt-readout">
          <span className="pt-readout-value">{latencyMs}ms</span>
          <span className="pt-readout-label">latency</span>
        </span>
      )}
      {approvalsCount > 0 && (
        <span className="pt-hud" style={{ marginLeft: 'auto', color: '#f5a524' }}>
          {approvalsCount} approval{approvalsCount > 1 ? 's' : ''} pending
        </span>
      )}
    </div>
  );
}
