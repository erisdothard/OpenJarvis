import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../lib/store';
import { fetchDigest, generateDigest, synthesizeSpeech } from '../lib/api';
import type { DigestData } from '../lib/api';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return 'Working late, sir.';
  if (hour < 12) return 'Good morning, sir.';
  if (hour < 18) return 'Good afternoon, sir.';
  return 'Good evening, sir.';
}

/** Check if digest is stale (older than 12 hours). */
function isStale(digest: DigestData): boolean {
  try {
    const generated = new Date(digest.generated_at).getTime();
    return Date.now() - generated > 12 * 60 * 60 * 1000;
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
  /** Whether TTS is currently playing */
  isSpeaking: boolean;
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
      URL.revokeObjectURL(currentAudioRef.current.src);
      currentAudioRef.current = null;
    }
    for (const a of audioQueueRef.current) {
      URL.revokeObjectURL(a.src);
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
      URL.revokeObjectURL(audio.src);
      currentAudioRef.current = null;
      playNextAudio();
    };
    audio.play().catch(() => playNextAudio());
  }, [setBriefingStatus]);

  /** Split text into sentences and queue TTS for each. */
  const speakText = useCallback(
    (text: string) => {
      setBriefingStatus('speaking');
      // Split on sentence boundaries
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
      return;
    }

    const greeting = getGreeting();
    // Strip any existing greeting from the digest text to avoid duplication
    // (the digest agent often prepends its own "Good morning, sir." etc.)
    const digestText = digest.text.replace(
      /^Good\s+(morning|afternoon|evening|night),?\s*(sir\.?)?\s*/i,
      '',
    );
    const fullText = `${greeting} Here's your situation.\n\n${digestText}`;
    setBriefingText(fullText);

    // Auto-play TTS only once per session
    const today = new Date().toDateString();
    const alreadyPlayed = sessionStorage.getItem(SESSION_KEY) === today;
    if (speechEnabled && !alreadyPlayed) {
      sessionStorage.setItem(SESSION_KEY, today);
      // Small delay so the text renders first
      setTimeout(() => {
        if (mountedRef.current) speakText(fullText);
      }, 500);
    }
  }, [speechEnabled, setBriefingStatus, setBriefingText, speakText]);

  // Run once on mount
  useEffect(() => {
    if (hasRunRef.current) return;
    hasRunRef.current = true;
    loadBriefing();
  }, [loadBriefing]);

  return {
    interrupt,
    refresh: loadBriefing,
    isSpeaking: playingRef.current,
  };
}
