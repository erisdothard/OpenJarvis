import { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import type { JarvisState } from '../../lib/store';
import './jarvis-orb.css';

interface JarvisOrbProps {
  state: JarvisState;
  audioLevel: number; // 0-1
  alwaysOnActive: boolean;
  className?: string;
}

const orbVariants = {
  idle: {
    scale: 1,
    filter: 'brightness(0.85) saturate(0.7)',
    transition: { duration: 0.8, ease: 'easeInOut' as const },
  },
  listening: {
    scale: 1.03,
    filter: 'brightness(1.1) saturate(1.2)',
    transition: { duration: 0.4, ease: 'easeOut' as const },
  },
  thinking: {
    scale: 0.97,
    filter: 'brightness(0.95) saturate(0.5)',
    transition: { duration: 0.3, ease: 'easeInOut' as const },
  },
  speaking: {
    scale: 1.05,
    filter: 'brightness(1.15) saturate(1.0)',
    transition: { duration: 0.5, ease: 'easeOut' as const },
  },
};

const stateLabels: Record<JarvisState, string> = {
  idle: '',
  listening: 'LISTENING',
  thinking: 'PROCESSING',
  speaking: 'SPEAKING',
};

export function JarvisOrb({ state, audioLevel, alwaysOnActive, className }: JarvisOrbProps) {
  const orbRef = useRef<HTMLDivElement>(null);

  // Push audio level as CSS custom property for GPU-friendly reactivity
  useEffect(() => {
    if (orbRef.current) {
      orbRef.current.style.setProperty('--audio-level', String(audioLevel));
    }
  }, [audioLevel]);

  return (
    <div
      className={`jarvis-orb-wrapper ${className ?? ''}`}
      ref={orbRef}
      data-state={state}
    >
      {/* Breath ring — outermost, always present */}
      <motion.div
        className="orb-breath"
        animate={{
          scale: state === 'idle' ? [1, 1.06, 1] : 1,
          opacity: state === 'idle' ? [0.3, 0.5, 0.3] : 0.6,
        }}
        transition={{
          duration: state === 'idle' ? 4 : 0.3,
          repeat: state === 'idle' ? Infinity : 0,
          ease: 'easeInOut',
        }}
      />

      {/* Audio-reactive glow ring */}
      <div className="orb-glow" data-state={state} />

      {/* Main orb body */}
      <motion.div
        className="orb-body"
        variants={orbVariants}
        animate={state}
      >
        {/* Spinning rim */}
        <div className="orb-rim" data-state={state} />

        {/* Platinum ball texture */}
        <div className="orb-ball" />

        {/* Gloss highlight */}
        <div className="orb-gloss" />

        {/* Sparkles */}
        <span className="orb-sparkle orb-s1" />
        <span className="orb-sparkle orb-s2" />
        <span className="orb-sparkle orb-s3" />
      </motion.div>

      {/* State indicator dot */}
      <AnimatePresence>
        {alwaysOnActive && (
          <motion.div
            className="orb-indicator"
            data-state={state}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
          />
        )}
      </AnimatePresence>

      {/* Floating state label */}
      <AnimatePresence mode="wait">
        {stateLabels[state] && (
          <motion.span
            key={state}
            className="orb-state-label"
            data-state={state}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.25 }}
          >
            {stateLabels[state]}
          </motion.span>
        )}
      </AnimatePresence>
    </div>
  );
}
