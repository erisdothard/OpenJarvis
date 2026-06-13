import { useEffect, useState, useCallback } from 'react';
import { listConnectors } from '../../lib/connectors-api';
import type { ConnectorInfo } from '../../types/connectors';

const SOCIAL_IDS = ['facebook', 'instagram', 'linkedin', 'twitter'];

const SOCIAL_META: Record<string, { label: string; icon: string }> = {
  facebook: { label: 'Facebook', icon: 'f' },
  instagram: { label: 'Instagram', icon: '◎' },
  linkedin: { label: 'LinkedIn', icon: 'in' },
  twitter: { label: 'X / Twitter', icon: '𝕏' },
};

type PostStatus = 'idle' | 'drafting' | 'publishing' | 'done' | 'error';

export function SocialPanel() {
  const [socials, setSocials] = useState<ConnectorInfo[]>([]);
  const [loading, setLoading] = useState(true);

  // Quick-post state
  const [showComposer, setShowComposer] = useState(false);
  const [postText, setPostText] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [postStatus, setPostStatus] = useState<PostStatus>('idle');
  const [statusMessage, setStatusMessage] = useState('');

  useEffect(() => {
    let cancelled = false;
    listConnectors()
      .then((all) => {
        if (!cancelled) {
          setSocials(all.filter((c) => SOCIAL_IDS.includes(c.connector_id)));
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const connectedPlatforms = socials.filter((c) => c.connected).map((c) => c.connector_id);

  const togglePlatform = useCallback((id: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    );
  }, []);

  const copyToChat = useCallback((text: string, label: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setPostStatus('done');
      setStatusMessage(`${label} copied — paste into chat to execute.`);
      setTimeout(() => { setPostStatus('idle'); setStatusMessage(''); }, 3000);
    }).catch(() => {
      setPostStatus('error');
      setStatusMessage('Copy failed. Check clipboard permissions.');
    });
  }, []);

  const handleDraft = useCallback(() => {
    if (!postText.trim()) return;
    const platforms = selectedPlatforms.length > 0 ? selectedPlatforms : connectedPlatforms;
    const prompt = `Use the content_draft tool to draft a post about: ${postText}\nPlatforms: ${platforms.join(', ')}`;
    copyToChat(prompt, 'Draft prompt');
  }, [postText, selectedPlatforms, connectedPlatforms, copyToChat]);

  const handlePublish = useCallback(() => {
    if (!postText.trim() || selectedPlatforms.length === 0) return;
    const prompt = `Use the social_publish tool with platforms=[${selectedPlatforms.map((p) => `"${p}"`).join(',')}] and content="${postText.replace(/"/g, '\\"')}"`;
    copyToChat(prompt, 'Publish command');
  }, [postText, selectedPlatforms, copyToChat]);

  return (
    <div className="pt-panel pt-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h3>Social Media</h3>
        {connectedPlatforms.length > 0 && (
          <button
            className="pt-btn pt-btn-sm"
            onClick={() => setShowComposer(!showComposer)}
            style={{
              fontSize: 10,
              padding: '3px 8px',
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.15)',
              background: showComposer ? 'rgba(255,255,255,0.1)' : 'transparent',
              color: 'inherit',
              cursor: 'pointer',
            }}
          >
            {showComposer ? '✕ Close' : '+ Post'}
          </button>
        )}
      </div>

      {loading ? (
        <div className="pt-hud pt-hud-dim" style={{ textAlign: 'center', padding: '12px 0', fontSize: 11 }}>
          Loading...
        </div>
      ) : socials.length === 0 ? (
        <div className="pt-hud pt-hud-dim" style={{ textAlign: 'center', padding: '12px 0', fontSize: 11 }}>
          No social connectors found
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {socials.map((c) => {
            const meta = SOCIAL_META[c.connector_id];
            const isSelected = selectedPlatforms.includes(c.connector_id);
            return (
              <div
                className="pt-row"
                key={c.connector_id}
                style={{
                  alignItems: 'center',
                  cursor: showComposer && c.connected ? 'pointer' : 'default',
                  opacity: showComposer && !c.connected ? 0.4 : 1,
                  background: showComposer && isSelected ? 'rgba(255,255,255,0.05)' : 'transparent',
                  borderRadius: 4,
                  padding: '2px 4px',
                }}
                onClick={() => {
                  if (showComposer && c.connected) togglePlatform(c.connector_id);
                }}
              >
                {showComposer && c.connected ? (
                  <span style={{ fontSize: 10, width: 14, textAlign: 'center', flexShrink: 0 }}>
                    {isSelected ? '◉' : '○'}
                  </span>
                ) : (
                  <span
                    className={`pt-dot ${c.connected ? 'pt-dot-run' : 'pt-dot-idle'}`}
                  />
                )}
                <span
                  className="pt-hud"
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    width: 18,
                    textAlign: 'center',
                    opacity: 0.5,
                    flexShrink: 0,
                  }}
                >
                  {meta?.icon}
                </span>
                <span style={{ fontSize: 13 }}>{meta?.label ?? c.display_name}</span>
                <span className="pt-meta pt-hud pt-hud-dim" style={{ marginLeft: 'auto' }}>
                  {c.connected ? (c.chunks ? `${c.chunks} indexed` : 'connected') : 'disconnected'}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Quick-post composer */}
      {showComposer && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <textarea
            value={postText}
            onChange={(e) => setPostText(e.target.value)}
            placeholder="What do you want to post?"
            style={{
              width: '100%',
              minHeight: 60,
              padding: 8,
              fontSize: 12,
              borderRadius: 6,
              border: '1px solid rgba(255,255,255,0.12)',
              background: 'rgba(0,0,0,0.3)',
              color: 'inherit',
              resize: 'vertical',
              fontFamily: 'inherit',
            }}
          />
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <button
              onClick={handleDraft}
              disabled={!postText.trim() || postStatus === 'drafting'}
              style={{
                fontSize: 10,
                padding: '4px 10px',
                borderRadius: 4,
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'transparent',
                color: 'inherit',
                cursor: postText.trim() ? 'pointer' : 'not-allowed',
                opacity: postText.trim() ? 1 : 0.4,
              }}
            >
              Draft with AI
            </button>
            <button
              onClick={handlePublish}
              disabled={!postText.trim() || selectedPlatforms.length === 0 || postStatus === 'publishing'}
              style={{
                fontSize: 10,
                padding: '4px 10px',
                borderRadius: 4,
                border: 'none',
                background: selectedPlatforms.length > 0 && postText.trim()
                  ? 'rgba(59,130,246,0.8)'
                  : 'rgba(255,255,255,0.08)',
                color: 'inherit',
                cursor: selectedPlatforms.length > 0 && postText.trim() ? 'pointer' : 'not-allowed',
              }}
            >
              Publish ({selectedPlatforms.length})
            </button>
          </div>
          {statusMessage && (
            <div
              style={{
                fontSize: 10,
                padding: '4px 8px',
                borderRadius: 4,
                background: postStatus === 'error'
                  ? 'rgba(239,68,68,0.15)'
                  : postStatus === 'done'
                    ? 'rgba(34,197,94,0.15)'
                    : 'rgba(255,255,255,0.05)',
                color: postStatus === 'error'
                  ? '#f87171'
                  : postStatus === 'done'
                    ? '#4ade80'
                    : 'inherit',
              }}
            >
              {statusMessage}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
