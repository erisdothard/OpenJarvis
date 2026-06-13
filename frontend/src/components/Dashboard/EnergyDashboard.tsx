import { useState, useEffect, useCallback } from 'react';
import {
  Calendar,
  CheckSquare,
  Mail,
  Cloud,
  HardDrive,
  Server,
  Clock,
  AlertTriangle,
} from 'lucide-react';
import { fetchGlance } from '../../lib/api';
import type { GlanceData } from '../../lib/api';

function GlanceCard({
  icon: Icon,
  label,
  children,
  accent,
}: {
  icon: typeof Calendar;
  label: string;
  children: React.ReactNode;
  accent?: string;
}) {
  return (
    <div className="hud-panel p-4" style={{ minHeight: 90 }}>
      <div className="flex items-center gap-2 mb-2">
        <Icon size={12} style={{ color: accent || 'var(--color-accent)' }} />
        <span className="hud-label">{label}</span>
      </div>
      <div style={{ color: 'var(--color-text)' }}>{children}</div>
    </div>
  );
}

function DiskBar({ percent }: { percent: number }) {
  const color =
    percent > 90
      ? 'var(--color-error, #ef4444)'
      : percent > 75
        ? 'var(--color-warning, #f59e0b)'
        : 'var(--color-accent, #38bdf8)';
  return (
    <div
      style={{
        height: 6,
        borderRadius: 3,
        background: 'rgba(255,255,255,0.06)',
        marginTop: 6,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          height: '100%',
          width: `${percent}%`,
          background: color,
          borderRadius: 3,
          transition: 'width 300ms ease',
        }}
      />
    </div>
  );
}

export function EnergyDashboard() {
  const [data, setData] = useState<GlanceData | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    const result = await fetchGlance();
    if (result) {
      setData(result);
      setError(false);
    } else {
      setError(true);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  if (error || !data) {
    return (
      <div className="hud-panel p-6">
        <h3 className="hud-label flex items-center gap-2 mb-4">
          <Clock size={12} style={{ color: 'var(--color-accent)' }} />
          Status
        </h3>
        <div
          className="h-48 flex items-center justify-center text-sm"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          <span className="hud-mono">
            {error ? 'cannot connect to server' : 'loading…'}
          </span>
        </div>
      </div>
    );
  }

  const overdueCount = data.reminders.filter((r) => r.overdue).length;
  const upcomingReminders = data.reminders.filter((r) => !r.overdue);

  return (
    <div className="hud-panel p-6">
      <h3 className="hud-label flex items-center gap-2 mb-4">
        <Clock size={12} style={{ color: 'var(--color-accent)' }} />
        At a Glance
      </h3>

      <div className="grid grid-cols-2 gap-3">
        {/* Today's Schedule */}
        <GlanceCard icon={Calendar} label="Today">
          {data.calendar.length === 0 ? (
            <div className="hud-mono text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
              No events today
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {data.calendar.slice(0, 4).map((e, i) => (
                <div key={i} style={{ fontSize: 13, lineHeight: 1.4 }}>
                  <span style={{ fontWeight: 500 }}>{e.summary}</span>
                  {!e.all_day && e.start && (
                    <span
                      className="hud-mono"
                      style={{
                        marginLeft: 6,
                        fontSize: 11,
                        color: 'var(--color-text-tertiary)',
                      }}
                    >
                      {formatEventTime(e.start)}
                    </span>
                  )}
                </div>
              ))}
              {data.calendar.length > 4 && (
                <span
                  className="hud-mono"
                  style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}
                >
                  +{data.calendar.length - 4} more
                </span>
              )}
            </div>
          )}
        </GlanceCard>

        {/* Reminders */}
        <GlanceCard
          icon={overdueCount > 0 ? AlertTriangle : CheckSquare}
          label={`Reminders${overdueCount > 0 ? ` · ${overdueCount} overdue` : ''}`}
          accent={overdueCount > 0 ? 'var(--color-warning, #f59e0b)' : undefined}
        >
          {data.reminders.length === 0 ? (
            <div className="hud-mono text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
              All clear
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {data.reminders
                .sort((a, b) => (a.overdue === b.overdue ? 0 : a.overdue ? -1 : 1))
                .slice(0, 4)
                .map((r, i) => (
                  <div
                    key={i}
                    style={{
                      fontSize: 13,
                      lineHeight: 1.4,
                      color: r.overdue
                        ? 'var(--color-warning, #f59e0b)'
                        : 'var(--color-text)',
                    }}
                  >
                    {r.name}
                  </div>
                ))}
              {data.reminders.length > 4 && (
                <span
                  className="hud-mono"
                  style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}
                >
                  +{data.reminders.length - 4} more
                </span>
              )}
            </div>
          )}
        </GlanceCard>

        {/* Email */}
        <GlanceCard icon={Mail} label="Inbox">
          <div className="hud-mono text-2xl font-semibold">
            {data.unread_emails !== null ? data.unread_emails : '—'}
            <span
              className="hud-label ml-2"
              style={{ fontSize: '0.625rem', letterSpacing: '0.18em' }}
            >
              UNREAD
            </span>
          </div>
        </GlanceCard>

        {/* Weather */}
        <GlanceCard icon={Cloud} label={data.weather?.location || 'Weather'}>
          {data.weather ? (
            <div>
              <span className="hud-mono text-2xl font-semibold">
                {data.weather.temp_f}°
              </span>
              <span
                style={{
                  marginLeft: 8,
                  fontSize: 13,
                  color: 'var(--color-text-secondary)',
                }}
              >
                {data.weather.condition}
              </span>
              <div
                className="hud-mono"
                style={{
                  fontSize: 11,
                  color: 'var(--color-text-tertiary)',
                  marginTop: 2,
                }}
              >
                Feels like {data.weather.feels_like_f}° · {data.weather.humidity}% humidity
              </div>
            </div>
          ) : (
            <div className="hud-mono text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
              Unavailable
            </div>
          )}
        </GlanceCard>

        {/* Disk */}
        <GlanceCard icon={HardDrive} label="Disk">
          <div className="hud-mono text-2xl font-semibold">
            {data.disk.free_gb}
            <span
              className="hud-label ml-1"
              style={{ fontSize: '0.625rem', letterSpacing: '0.18em' }}
            >
              GB FREE
            </span>
          </div>
          <DiskBar percent={data.disk.percent_used} />
          <div
            className="hud-mono"
            style={{
              fontSize: 11,
              color: 'var(--color-text-tertiary)',
              marginTop: 4,
            }}
          >
            {data.disk.used_gb} / {data.disk.total_gb} GB used
          </div>
        </GlanceCard>

        {/* Ollama */}
        <GlanceCard icon={Server} label="Ollama">
          {data.ollama.running ? (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: 'var(--color-success, #22c55e)',
                    display: 'inline-block',
                  }}
                />
                <span className="hud-mono" style={{ fontSize: 13, fontWeight: 500 }}>
                  Running
                </span>
              </div>
              {data.ollama.models.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {data.ollama.models.map((m, i) => (
                    <span
                      key={i}
                      className="hud-mono"
                      style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}
                    >
                      {m.name} ({m.size_gb}GB)
                    </span>
                  ))}
                </div>
              ) : (
                <span
                  className="hud-mono"
                  style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}
                >
                  No models loaded
                </span>
              )}
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'var(--color-text-tertiary, #666)',
                  display: 'inline-block',
                }}
              />
              <span className="hud-mono" style={{ fontSize: 13, color: 'var(--color-text-tertiary)' }}>
                Offline
              </span>
            </div>
          )}
        </GlanceCard>
      </div>
    </div>
  );
}

function formatEventTime(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return dateStr;
  }
}
