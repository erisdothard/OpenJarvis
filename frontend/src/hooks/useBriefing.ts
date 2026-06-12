import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../lib/store';
import {
  fetchDigest,
  generateDigest,
  getDigestAudioUrl,
  synthesizeSpeech,
  currentDigestType,
} from '../lib/api';
import type { DigestData, DigestType } from '../lib/api';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return 'Working late, sir.';
  if (hour < 12) return 'Good morning, sir.';
  if (hour < 18) return 'Good afternoon, sir.';
  return 'Good evening, sir.';
}

function getLiveDateTimeAnnouncement(): string {
  const now = new Date();
  const day = now.toLocaleDateString('en-US', { weekday: 'long' });
  const month = now.toLocaleDateString('en-US', { month: 'long' });
  const date = now.getDate();
  const year = now.getFullYear();
  const time = now.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
  return `It is ${day}, ${month} ${date}, ${year}. The time is ${time}.`;
}

function getDigestLabel(type: DigestType): string {
  if (type === 'midday') return "Here's your midday update.";
  if (type === 'evening') return "Here's your evening wrap-up.";
  return "Here's your situation.";
}

/** Check if digest is stale. Morning/evening: 6h window. Midday: 4h window. */
function isStale(digest: DigestData): boolean {
  try {
    const generated = new Date(digest.generated_at);
    const now = new Date();
    const sameDay =
      generated.getFullYear() === now.getFullYear() &&
      generated.getMonth() === now.getMonth() &&
      generated.getDate() === now.getDate();
    if (!sameDay) return true;
    // Midday updates have a shorter freshness window
    const maxAge = digest.digest_type === 'midday' ? 4 : 6;
    return now.getTime() - generated.getTime() > maxAge * 60 * 60 * 1000;
  } catch {
    return true;
  }
}

const SESSION_KEY = 'oj-briefing-played';

export interface BriefingControls {
  /** Stop all TTS playback */
  interrupt: () => void;
  /** Re-fetch and re-play the briefing */
  refresh: () => void;
  /** Play (or replay) the briefing audio on demand */
  play: () => void;
  /** Whether TTS is currently playing */
  isSpeaking: boolean;
  /** Whether pre-generated audio is available */
  hasAudio: boolean;
}

export function useBriefing(): BriefingControls {
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const setBriefingStatus = useAppStore((s) => s.setBriefingStatus);
  const setBriefingText = useAppStore((s) => s.setBriefingText);
  const setBriefingError = useAppStore((s) => s.setBriefingError);

  const audioQueueRef = useRef<HTMLAudioElement[]>([]);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const playingRef = useRef(false);
  const mountedRef = useRef(true);
  const hasRunRef = useRef(false);
  const hasAudioRef = useRef(false);
  const digestTextRef = useRef<string | null>(null);

  // Clean up on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const interrupt = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      if (currentAudioRef.current.src.startsWith('blob:')) {
        URL.revokeObjectURL(currentAudioRef.current.src);
      }
      currentAudioRef.current = null;
    }
    for (const a of audioQueueRef.current) {
      if (a.src.startsWith('blob:')) URL.revokeObjectURL(a.src);
    }
    audioQueueRef.current = [];
    playingRef.current = false;
    if (mountedRef.current) {
      const st = useAppStore.getState().briefing.status;
      if (st === 'speaking') setBriefingStatus('ready');
    }
  }, [setBriefingStatus]);

  const playNextAudio = useCallback(() => {
    if (audioQueueRef.current.length === 0) {
      playingRef.current = false;
      currentAudioRef.current = null;
      if (mountedRef.current) setBriefingStatus('ready');
      return;
    }
    playingRef.current = true;
    const audio = audioQueueRef.current.shift()!;
    currentAudioRef.current = audio;
    audio.onended = () => {
      if (audio.src.startsWith('blob:')) URL.revokeObjectURL(audio.src);
      currentAudioRef.current = null;
      playNextAudio();
    };
    audio.play().catch(() => playNextAudio());
  }, [setBriefingStatus]);

  /** Play pre-generated digest audio from the backend. */
  const playDigestAudio = useCallback(() => {
    if (!mountedRef.current) return;
    interrupt();
    setBriefingStatus('speaking');
    const audio = new Audio(getDigestAudioUrl());
    audioQueueRef.current.push(audio);
    playingRef.current = false; // let playNextAudio set it
    playNextAudio();
  }, [interrupt, playNextAudio, setBriefingStatus]);

  /** Fall back to sentence-level TTS synthesis. */
  const speakText = useCallback(
    (text: string) => {
      setBriefingStatus('speaking');
      const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
      for (const sentence of sentences) {
        const clean = sentence
          .replace(/[#*_~`>\[\]]/g, '')
          .replace(/\n+/g, ' ')
          .trim();
        if (!clean) continue;
        synthesizeSpeech(clean)
          .then((blob) => {
            if (!mountedRef.current) return;
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audioQueueRef.current.push(audio);
            if (!playingRef.current) playNextAudio();
          })
          .catch(() => {
            // TTS failure for this sentence — skip it
          });
      }
    },
    [playNextAudio, setBriefingStatus],
  );

  /** Play the briefing: always announce live date/time first, then digest. */
  const play = useCallback(() => {
    interrupt();
    setBriefingStatus('speaking');
    const announcement = getLiveDateTimeAnnouncement();

    synthesizeSpeech(announcement)
      .then((blob) => {
        if (!mountedRef.current) return;
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioQueueRef.current.push(audio);

        if (hasAudioRef.current) {
          // Queue pre-generated digest audio after the announcement
          const digestAudio = new Audio(getDigestAudioUrl());
          audioQueueRef.current.push(digestAudio);
        } else if (digestTextRef.current) {
          // Queue sentence-level TTS after the announcement
          const sentences = digestTextRef.current.match(/[^.!?]+[.!?]+/g) || [digestTextRef.current];
          for (const sentence of sentences) {
            const clean = sentence.replace(/[#*_~`>\[\]]/g, '').replace(/\n+/g, ' ').trim();
            if (!clean) continue;
            synthesizeSpeech(clean)
              .then((b) => {
                if (!mountedRef.current) return;
                const u = URL.createObjectURL(b);
                audioQueueRef.current.push(new Audio(u));
                if (!playingRef.current) playNextAudio();
              })
              .catch(() => {});
          }
        }

        if (!playingRef.current) playNextAudio();
      })
      .catch(() => {
        // If date/time TTS fails, fall back to original behavior
        if (hasAudioRef.current) {
          playDigestAudio();
        } else if (digestTextRef.current) {
          speakText(digestTextRef.current);
        }
      });
  }, [interrupt, playDigestAudio, speakText, playNextAudio, setBriefingStatus]);

  const loadBriefing = useCallback(async () => {
    setBriefingStatus('loading');

    const digestType = currentDigestType();
    let digest = await fetchDigest(digestType);

    // If no digest or stale, generate one
    if (!digest || isStale(digest)) {
      setBriefingStatus('generating');
      digest = await generateDigest(digestType);
    }

    if (!mountedRef.current) return;

    if (!digest) {
      const fallback = `${getGreeting()} Your daily briefing isn't available right now. What would you like to work on?`;
      setBriefingText(fallback);
      digestTextRef.current = fallback;
      hasAudioRef.current = false;
      return;
    }

    hasAudioRef.current = !!digest.audio_available;

    const greeting = getGreeting();
    const label = getDigestLabel(digest.digest_type || digestType);
    // Strip any existing greeting from the digest text to avoid duplication
    const digestText = digest.text.replace(
      /^Good\s+(morning|afternoon|evening|night),?\s*(sir\.?)?\s*/i,
      '',
    );
    const fullText = `${greeting} ${label}\n\n${digestText}`;
    setBriefingText(fullText, digest.follow_up_questions || []);
    digestTextRef.current = fullText;

    // Auto-play only once per session per digest type
    const sessionKey = `${SESSION_KEY}-${digest.digest_type || digestType}`;
    const today = new Date().toDateString();
    const alreadyPlayed = sessionStorage.getItem(sessionKey) === today;
    if (speechEnabled && !alreadyPlayed) {
      sessionStorage.setItem(sessionKey, today);
      setTimeout(() => {
        if (!mountedRef.current) return;
        play();
      }, 500);
    }
  }, [speechEnabled, setBriefingStatus, setBriefingText, play]);

  // Run once on mount
  useEffect(() => {
    if (hasRunRef.current) return;
    hasRunRef.current = true;
    loadBriefing();
  }, [loadBriefing]);

  return {
    interrupt,
    refresh: loadBriefing,
    play,
    isSpeaking: playingRef.current,
    hasAudio: hasAudioRef.current,
  };
}
