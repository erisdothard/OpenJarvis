"""ElevenLabs text-to-speech backend.

Uses the ElevenLabs REST API for high-quality voice synthesis.
Requires ELEVENLABS_API_KEY environment variable or config.
"""

from __future__ import annotations

import os
from typing import List

import httpx

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


def _elevenlabs_synthesize(
    api_key: str,
    text: str,
    voice_id: str,
    model: str = "eleven_multilingual_v2",
    output_format: str = "mp3_44100_128",
    speed: float = 1.0,
) -> bytes:
    """Call the ElevenLabs TTS API and return raw audio bytes."""
    resp = httpx.post(
        f"{_ELEVENLABS_API_BASE}/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
        params={"output_format": output_format},
        json={
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": speed,
            },
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.content


@TTSRegistry.register("elevenlabs")
class ElevenLabsTTSBackend(TTSBackend):
    """ElevenLabs TTS backend — high-quality voice cloning and synthesis."""

    backend_id = "elevenlabs"

    def __init__(
        self, *, api_key: str = "", model: str = "eleven_multilingual_v2"
    ) -> None:
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self._model = model

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")

        if not voice_id:
            voice_id = "onwK4e9ZLuTAKqWW03F9"

        # Map simple format names to ElevenLabs format strings
        fmt_map = {
            "mp3": "mp3_44100_128",
            "pcm": "pcm_44100",
        }
        el_format = fmt_map.get(output_format, output_format)

        audio = _elevenlabs_synthesize(
            self._api_key,
            text,
            voice_id=voice_id,
            model=self._model,
            output_format=el_format,
            speed=speed,
        )

        return TTSResult(
            audio=audio,
            format="mp3",
            voice_id=voice_id,
            sample_rate=44100,
            metadata={"backend": "elevenlabs", "model": self._model},
        )

    def available_voices(self) -> List[str]:
        if not self._api_key:
            return []
        resp = httpx.get(
            f"{_ELEVENLABS_API_BASE}/voices",
            headers={"xi-api-key": self._api_key},
            timeout=30.0,
        )
        resp.raise_for_status()
        return [v["voice_id"] for v in resp.json().get("voices", [])]

    def health(self) -> bool:
        return bool(self._api_key)
