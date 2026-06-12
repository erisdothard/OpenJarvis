"""Silero VAD wrapper for server-side voice activity detection.

Uses the Silero VAD ONNX model to detect speech start/end in real-time
audio streams. Far more accurate than client-side RMS thresholds.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

# Audio constants — Silero VAD expects 16kHz mono
SAMPLE_RATE = 16000
# Silero processes 512-sample chunks at 16kHz (32ms)
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2  # 16-bit PCM = 2 bytes per sample


class VadState(str, Enum):
    IDLE = "idle"
    SPEAKING = "speaking"


class VadEvent(NamedTuple):
    """Event emitted by the VAD state machine."""
    type: str  # "speech_start", "speech_end", "none"
    speech_audio: bytes | None = None  # PCM bytes of the speech segment (on speech_end)


class SileroVAD:
    """Real-time voice activity detector using Silero VAD."""

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        min_silence_ms: int = 800,
        speech_pad_ms: int = 300,
    ):
        self.threshold = threshold
        self.min_speech_frames = max(1, int(min_speech_ms / 32))  # 32ms per chunk
        self.min_silence_frames = max(1, int(min_silence_ms / 32))
        self.speech_pad_frames = max(0, int(speech_pad_ms / 32))

        self._state = VadState.IDLE
        self._speech_frame_count = 0
        self._silence_frame_count = 0

        # Ring buffer: keeps recent audio for padding before speech start
        self._pre_buffer: list[bytes] = []
        self._pre_buffer_max = self.speech_pad_frames + 5  # extra margin

        # Accumulates audio during speech
        self._speech_buffer: list[bytes] = []

        # Silero model
        self._model = None
        self._load_model()

    def _load_model(self) -> None:
        """Load the Silero VAD model."""
        try:
            from silero_vad import load_silero_vad
            self._model = load_silero_vad()
            logger.info("Silero VAD model loaded")
        except Exception as exc:
            logger.error("Failed to load Silero VAD: %s", exc)
            raise

    def reset(self) -> None:
        """Reset state machine and buffers."""
        self._state = VadState.IDLE
        self._speech_frame_count = 0
        self._silence_frame_count = 0
        self._pre_buffer.clear()
        self._speech_buffer.clear()
        if self._model is not None:
            self._model.reset_states()

    def process_chunk(self, pcm_bytes: bytes) -> VadEvent:
        """Process a 512-sample (32ms) chunk of 16-bit PCM audio.

        Returns a VadEvent indicating what happened.
        """
        if self._model is None:
            return VadEvent(type="none")

        # Convert 16-bit PCM to float32 tensor
        import torch
        samples = np.frombuffer(pcm_bytes[:CHUNK_BYTES], dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) < CHUNK_SAMPLES:
            # Pad short chunks with silence
            samples = np.pad(samples, (0, CHUNK_SAMPLES - len(samples)))
        audio_tensor = torch.from_numpy(samples)

        # Get speech probability
        prob = self._model(audio_tensor, SAMPLE_RATE).item()

        is_speech = prob > self.threshold

        if self._state == VadState.IDLE:
            # Keep pre-buffer for padding
            self._pre_buffer.append(pcm_bytes[:CHUNK_BYTES])
            if len(self._pre_buffer) > self._pre_buffer_max:
                self._pre_buffer.pop(0)

            if is_speech:
                self._speech_frame_count += 1
                if self._speech_frame_count >= self.min_speech_frames:
                    # Transition to SPEAKING
                    self._state = VadState.SPEAKING
                    self._silence_frame_count = 0
                    # Include pre-buffer for context
                    self._speech_buffer = list(self._pre_buffer)
                    self._pre_buffer.clear()
                    return VadEvent(type="speech_start")
            else:
                self._speech_frame_count = 0

        elif self._state == VadState.SPEAKING:
            self._speech_buffer.append(pcm_bytes[:CHUNK_BYTES])

            if not is_speech:
                self._silence_frame_count += 1
                if self._silence_frame_count >= self.min_silence_frames:
                    # Transition to IDLE — speech ended
                    speech_audio = b"".join(self._speech_buffer)
                    self._state = VadState.IDLE
                    self._speech_frame_count = 0
                    self._silence_frame_count = 0
                    self._speech_buffer.clear()
                    self.reset_model_states()
                    return VadEvent(type="speech_end", speech_audio=speech_audio)
            else:
                self._silence_frame_count = 0

        return VadEvent(type="none")

    def force_end(self) -> VadEvent:
        """Force end-of-speech (e.g. user pressed commit button)."""
        if self._state == VadState.SPEAKING and self._speech_buffer:
            speech_audio = b"".join(self._speech_buffer)
            self._state = VadState.IDLE
            self._speech_frame_count = 0
            self._silence_frame_count = 0
            self._speech_buffer.clear()
            self.reset_model_states()
            return VadEvent(type="speech_end", speech_audio=speech_audio)
        return VadEvent(type="none")

    def reset_model_states(self) -> None:
        """Reset the Silero model's internal RNN states."""
        if self._model is not None:
            self._model.reset_states()

    @property
    def state(self) -> VadState:
        return self._state


def silero_available() -> bool:
    """Check if Silero VAD can be loaded."""
    try:
        from silero_vad import load_silero_vad  # noqa: F401
        return True
    except ImportError:
        return False
