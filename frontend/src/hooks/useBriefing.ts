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
import type { AudioManagerReturn } from './useAudioManager';

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

function isStale(digest: DigestData): boolean {
  try {
    const generated = new Date(digest.generated_at);
    const now = new Date();
    const sameDay =
      generated.getFullYear() === now.getFullYear() &&
      generated.getMonth() === now.getMonth() &&
      generated.getDate() === now.getDate();
    if (!sameDay) return true;
    const maxAge = digest.digest_type === 'midday' ? 4 : 6;
    return now.getTime() - generated.getTime() > maxAge * 60 * 60 * 1000;
  } catch {
    return true;
  }
}

const SESSION_KEY = 'oj-briefing-played';

export interface BriefingControls {
  interrupt: () => void;
  refresh: () => void;
  play: () => void;
  isSpeaking: boolean;
  hasAudio: boolean;
}

/**
 * Daily briefing hook.  Delegates all audio playback to the shared
 * AudioManager so there is only one queue and one interrupt path.
 */
export function useBriefing(audioManager: AudioManagerReturn): BriefingControls {
  const speechEnabled = useAppStore((s) => s.settings.speechEnabled);
  const setBriefingStatus = useAppStore((s) => s.setBriefingStatus);
  const setBriefingText = useAppStore((s) => s.setBriefingText);
  const setBriefingError = useAppStore((s) => s.setBriefingError);

  const mountedRef = useRef(true);
  const hasRunRef = useRef(false);
  const hasAudioRef = useRef(false);
  const digestTextRef = useRef<string | null>(null);

  // We track speaking locally via the AudioManager's callback.
  // Because the AudioManager is shared, any new audio source (chat TTS,
  // voice stream) will interrupt the briefing automatically.
  const speakingRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const interrupt = useCallback(() => {
    audioManager.interruptAll();
    speakingRef.current = false;
    if (mountedRef.current) {
      const st = useAppStore.getState().briefing.status;
      if (st === 'speaking') setBriefingStatus('ready');
    }
  }, [audioManager, setBriefingStatus]);

  /** Synthesize a single sentence and enqueue via AudioManager. */
  const enqueueSentence = useCallback((text: string, gen: number) => {
    const clean = text.replace(/[#*_~`>\[\]]/g, '').replace(/\n+/g, ' ').trim();
    if (!clean) return;
    synthesizeSpeech(clean)
      .then((blob) => {
        if (!mountedRef.current) return;
        const url = URL.createObjectURL(blob);
        audioManager.enqueueBlob(url, gen);
      })
      .catch((err) => {
        console.warn('[Briefing] TTS sentence failed:', err?.message || err);
        if (mountedRef.current) {
          speakingRef.current = false;
          setBriefingStatus('ready');
        }
      });
  }, [audioManager, setBriefingStatus]);

  const play = useCallback(() => {
    audioManager.interruptAll();
    setBriefingStatus('speaking');
    speakingRef.current = true;

    const gen = audioManager.getGeneration();
    const announcement = getLiveDateTimeAnnouncement();

    synthesizeSpeech(announcement)
      .then((blob) => {
        if (!mountedRef.current) return;
        audioManager.enqueueBlob(URL.createObjectURL(blob), gen);

        if (hasAudioRef.current) {
          // Queue pre-generated digest audio after the announcement
          audioManager.enqueueBlob(getDigestAudioUrl(), gen);
        } else if (digestTextRef.current) {
          const sentences = digestTextRef.current.match(/[^.!?]+[.!?]+/g) || [digestTextRef.current];
          for (const s of sentences) enqueueSentence(s, gen);
        }
      })
      .catch((err) => {
        console.warn('[Briefing] Announcement TTS failed:', err?.message || err);
        // Date/time TTS failed — fall back to digest audio/sentences
        if (hasAudioRef.current) {
          audioManager.enqueueBlob(getDigestAudioUrl(), gen);
        } else if (digestTextRef.current) {
          const sentences = digestTextRef.current.match(/[^.!?]+[.!?]+/g) || [digestTextRef.current];
          for (const s of sentences) enqueueSentence(s, gen);
        } else {
          // Nothing to fall back to — reset speaking state
          speakingRef.current = false;
          if (mountedRef.current) setBriefingStatus('ready');
        }
      });
  }, [audioManager, setBriefingStatus, enqueueSentence]);

  const loadBriefing = useCallback(async () => {
    setBriefingStatus('loading');

    const digestType = currentDigestType();
    let digest = await fetchDigest(digestType);

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
    const digestText = digest.text.replace(
      /^Good\s+(morning|afternoon|evening|night),?\s*(sir\.?)?\s*/i,
      '',
    );
    const fullText = `${greeting} ${label}\n\n${digestText}`;
    setBriefingText(fullText, digest.follow_up_questions || []);
    digestTextRef.current = fullText;

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

  useEffect(() => {
    if (hasRunRef.current) return;
    hasRunRef.current = true;
    loadBriefing();
  }, [loadBriefing]);

  // When AudioManager finishes (someone else interrupted or queue drained),
  // update briefing status if we were speaking.
  useEffect(() => {
    if (!audioManager.isPlaying && speakingRef.current) {
      speakingRef.current = false;
      if (mountedRef.current) setBriefingStatus('ready');
    }
  }, [audioManager.isPlaying, setBriefingStatus]);

  return {
    interrupt,
    refresh: loadBriefing,
    play,
    isSpeaking: speakingRef.current,
    hasAudio: hasAudioRef.current,
  };
}
