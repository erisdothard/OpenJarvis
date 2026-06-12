import { useGlance } from '../../hooks/useGlance';

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return iso;
  }
}

export function SituationalPanel() {
  const { data, loading } = useGlance();

  if (loading && !data) {
    return (
      <div className="pt-panel pt-card">
        <h3>Situational</h3>
        <div className="pt-hud pt-hud-dim" style={{ textAlign: 'center', padding: '12px 0' }}>
          Loading...
        </div>
      </div>
    );
  }

  if (!data) return null;

  const hasCalendar = data.calendar && data.calendar.length > 0;
  const hasReminders = data.reminders && data.reminders.length > 0;
  const hasEmail = data.unread_emails !== null && data.unread_emails !== undefined;
  const hasWeather = data.weather !== null;

  if (!hasCalendar && !hasReminders && !hasEmail && !hasWeather) return null;

  return (
    <div className="pt-panel pt-card">
      <h3>Situational</h3>

      {/* Calendar */}
      {hasCalendar && (
        <div style={{ marginBottom: 10 }}>
          {data.calendar.map((evt, i) => (
            <div className="pt-row" key={i}>
              <span className="pt-dot pt-dot-run" />
              <span className="pt-hud" style={{ color: '#eef0f4', minWidth: 64, flexShrink: 0 }}>
                {evt.all_day ? 'All day' : formatTime(evt.start)}
              </span>
              <span style={{ fontSize: '13.5px', color: '#eef0f4' }}>{evt.summary}</span>
            </div>
          ))}
        </div>
      )}

      {/* Reminders */}
      {hasReminders && (
        <div style={{ marginBottom: 10 }}>
          {data.reminders.map((rem, i) => (
            <div className="pt-row" key={i}>
              <span className={`pt-dot ${rem.overdue ? 'pt-dot-warn pt-overdue' : 'pt-dot-idle'}`} />
              <span style={{ fontSize: '13.5px', color: rem.overdue ? '#f5a524' : '#eef0f4' }}>
                {rem.name}
              </span>
              {rem.overdue && (
                <span className="pt-hud" style={{ marginLeft: 'auto', color: '#f5a524', fontSize: 9 }}>
                  OVERDUE
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Unread emails */}
      {hasEmail && (
        <div className="pt-row">
          <span className={`pt-dot ${data.unread_emails! > 0 ? 'pt-dot-run' : 'pt-dot-idle'}`} />
          <span style={{ fontSize: '13.5px', color: '#eef0f4' }}>
            {data.unread_emails} unread email{data.unread_emails !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Weather */}
      {hasWeather && data.weather && (
        <div className="pt-row" style={{ borderTop: '1px solid rgba(225,228,240,0.07)' }}>
          <span className="pt-hud" style={{ color: '#eef0f4', letterSpacing: '0.04em' }}>
            {data.weather.temp_f}°F {data.weather.condition}
          </span>
          <span className="pt-hud pt-hud-dim" style={{ marginLeft: 'auto' }}>
            {data.weather.location}
          </span>
        </div>
      )}
    </div>
  );
}
