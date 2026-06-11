import { useState, useEffect, useCallback, useRef } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  BarVisualizer,
} from '@livekit/components-react';
import '@livekit/components-styles';
import { Mic, MicOff, Phone, PhoneOff } from 'lucide-react';
import { getBase } from '../../lib/api';

type SessionState = 'idle' | 'connecting' | 'active' | 'error';

interface TokenData {
  token: string;
  url: string;
  room: string;
}

function AgentVisualizer({ onDisconnect }: { onDisconnect: () => void }) {
  const { state, audioTrack } = useVoiceAssistant();

  const stateLabel: Record<string, string> = {
    disconnected: 'OFFLINE',
    connecting: 'CONNECTING',
    initializing: 'INITIALIZING',
    listening: 'LISTENING',
    thinking: 'PROCESSING',
    speaking: 'SPEAKING',
  };

  return (
    <div className="voice-agent-panel">
      <div className="voice-agent-status">
        <div
          className="voice-agent-indicator"
          data-state={state}
        />
        <span className="voice-agent-label">
          {stateLabel[state] || state?.toUpperCase() || 'UNKNOWN'}
        </span>
      </div>

      <div className="voice-agent-visualizer">
        {audioTrack ? (
          <BarVisualizer state={state} trackRef={audioTrack} />
        ) : (
          <div className="voice-agent-idle-bars">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="voice-agent-idle-bar" />
            ))}
          </div>
        )}
      </div>

      <button
        onClick={onDisconnect}
        className="voice-agent-disconnect"
        title="End voice session"
      >
        <PhoneOff size={14} />
      </button>
    </div>
  );
}

export function VoiceAgent() {
  const [sessionState, setSessionState] = useState<SessionState>('idle');
  const [tokenData, setTokenData] = useState<TokenData | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const checkedRef = useRef(false);

  // Check LiveKit availability once on mount — verify the token endpoint
  // actually works, not just that env vars are set (the SDK may be missing).
  useEffect(() => {
    if (checkedRef.current) return;
    checkedRef.current = true;
    fetch(`${getBase()}/v1/livekit/token?room=probe`)
      .then((r) => {
        // 501 = SDK not installed, 503 = env vars missing
        setAvailable(r.ok);
      })
      .catch(() => setAvailable(false));
  }, []);

  const connect = useCallback(async () => {
    setSessionState('connecting');
    try {
      const res = await fetch(`${getBase()}/v1/livekit/token?room=jarvis`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setTokenData({ token: data.token, url: data.url, room: data.room });
      setSessionState('active');
    } catch {
      setSessionState('error');
      setTimeout(() => setSessionState('idle'), 3000);
    }
  }, []);

  const dispatchAgent = useCallback(() => {
    if (!tokenData?.room) return;
    fetch(`${getBase()}/v1/livekit/dispatch?room=${tokenData.room}`).catch(() => {});
  }, [tokenData]);

  const disconnect = useCallback(() => {
    setTokenData(null);
    setSessionState('idle');
  }, []);

  // Don't render anything if LiveKit isn't available
  if (available === false) return null;
  if (available === null) return null;

  // Active session — render the LiveKit room
  if (sessionState === 'active' && tokenData) {
    return (
      <LiveKitRoom
        serverUrl={tokenData.url}
        token={tokenData.token}
        connect={true}
        audio={true}
        onConnected={dispatchAgent}
        onDisconnected={disconnect}
        style={{ background: 'transparent' }}
      >
        <RoomAudioRenderer />
        <AgentVisualizer onDisconnect={disconnect} />
      </LiveKitRoom>
    );
  }

  // Idle / connecting — render the connect button
  return (
    <button
      onClick={connect}
      disabled={sessionState === 'connecting'}
      className="voice-agent-connect"
      title="Start voice session with Jarvis"
    >
      {sessionState === 'connecting' ? (
        <Phone size={14} className="voice-agent-connecting-icon" />
      ) : (
        <Mic size={14} />
      )}
      <span>{sessionState === 'connecting' ? 'CONNECTING' : 'VOICE'}</span>
    </button>
  );
}
