import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../lib/store';
import { fetchDigest, generateDigest, getDigestAudioUrl, synthesizeSpeech } from '../lib/api';
import type { DigestData } from '../lib/api';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return 'Working late, sir.';
  if (hour < 12) return 'Good morning, sir.';
  if (hour < 18) return 'Good afternoon, sir.';
  return 'Good evening, sir.';
}

/** Check if digest is stale (generated on a different calendar day or older than 6 hours). */
function isStale(digest: DigestData): boolean {
  try {
    const generated = new Date(digest.generated_at);
    const now = new Date();
    const sameDay =
      generated.getFullYear() === now.getFullYear() &&
      generated.getMonth() === now.getMonth() &&
      generated.getDate() === now.getDate();
    if (!sameDay) return true;
    return now.getTime() - generated.getTime() > 6 * 60 * 60 * 1000;
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

  /** Play the briefing: use pre-generated audio if available, else TTS. */
  const play = useCallback(() => {
    if (hasAudioRef.current) {
      playDigestAudio();
    } else if (digestTextRef.current) {
      speakText(digestTextRef.current);
    }
  }, [playDigestAudio, speakText]);

  const loadBriefing = useCallback(async () => {
    setBriefingStatus('loading');

    let digest = await fetchDigest();

    // If no digest or stale, generate one
    if (!digest || isStale(digest)) {
      setBriefingStatus('generating');
      digest = await generateDigest();
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
    // Strip any existing greeting from the digest text to avoid duplication
    const digestText = digest.text.replace(
      /^Good\s+(morning|afternoon|evening|night),?\s*(sir\.?)?\s*/i,
      '',
    );
    const fullText = `${greeting} Here's your situation.\n\n${digestText}`;
    setBriefingText(fullText);
    digestTextRef.current = fullText;

    // Auto-play only once per session
    const today = new Date().toDateString();
    const alreadyPlayed = sessionStorage.getItem(SESSION_KEY) === today;
    if (speechEnabled && !alreadyPlayed) {
      sessionStorage.setItem(SESSION_KEY, today);
      setTimeout(() => {
        if (!mountedRef.current) return;
        if (hasAudioRef.current) {
          playDigestAudio();
        } else {
          speakText(fullText);
        }
      }, 500);
    }
  }, [speechEnabled, setBriefingStatus, setBriefingText, speakText, playDigestAudio]);

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
