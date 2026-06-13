"""OpenAI TTS backend — cloud-based voice synthesis via OpenAI API."""

from __future__ import annotations

import logging
import os
from typing import List

import httpx

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)

_OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
_VALID_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"}
_MAX_INPUT_LENGTH = 4096
_DEFAULT_VOICE = "fable"


def _openai_tts_request(
    api_key: str,
    text: str,
    voice: str,
    model: str = "tts-1",
    speed: float = 1.0,
    response_format: str = "mp3",
) -> bytes:
    """Call the OpenAI TTS API and return raw audio bytes."""
    resp = httpx.post(
        _OPENAI_TTS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "input": text,
            "voice": voice,
            "speed": speed,
            "response_format": response_format,
        },
        timeout=120.0,
    )
    if not resp.is_success:
        body = resp.text[:500] if resp.text else "(empty)"
        logger.error(
            "OpenAI TTS %s: %s | voice=%s model=%s len=%d | body: %s",
            resp.status_code, resp.reason_phrase, voice, model, len(text), body,
        )
        resp.raise_for_status()
    return resp.content


@TTSRegistry.register("openai_tts")
class OpenAITTSBackend(TTSBackend):
    """OpenAI TTS backend — cloud synthesis."""

    backend_id = "openai_tts"

    def __init__(self, *, api_key: str = "", model: str = "tts-1") -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "nova",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        # Validate / default voice
        if not voice_id or voice_id not in _VALID_VOICES:
            if voice_id:
                logger.warning("Invalid OpenAI TTS voice '%s', falling back to '%s'", voice_id, _DEFAULT_VOICE)
            voice_id = _DEFAULT_VOICE

        # Truncate oversized input to avoid 400
        if len(text) > _MAX_INPUT_LENGTH:
            logger.warning("TTS input truncated from %d to %d chars", len(text), _MAX_INPUT_LENGTH)
            text = text[:_MAX_INPUT_LENGTH]

        # Clamp speed to valid range
        speed = max(0.25, min(4.0, speed))

        audio = _openai_tts_request(
            self._api_key,
            text,
            voice=voice_id,
            model=self._model,
            speed=speed,
            response_format=output_format,
        )

        return TTSResult(
            audio=audio,
            format=output_format,
            voice_id=voice_id,
            metadata={"backend": "openai_tts", "model": self._model},
        )

    def available_voices(self) -> List[str]:
        return sorted(_VALID_VOICES)

    def health(self) -> bool:
        return bool(self._api_key)
