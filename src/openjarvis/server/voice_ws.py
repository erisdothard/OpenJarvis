"""WebSocket streaming voice pipeline.

Single persistent WebSocket replaces the record→upload→transcribe→LLM→TTS
relay with real-time streaming:

  Client mic PCM → Server VAD → faster-whisper → LLM → Cartesia TTS → Client speaker

Protocol:
  - Binary frames: raw PCM audio (client→server: 16-bit 16kHz, server→client: float32 24kHz)
  - Text frames: JSON control/event messages
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tempfile
import time
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from openjarvis.core.types import Message, Role

logger = logging.getLogger("openjarvis.voice_ws")

router = APIRouter(tags=["voice"])


def _ensure_cloud_keys_loaded() -> None:
    """Load API keys from ~/.openjarvis/cloud-keys.env into os.environ.

    Mirrors CloudEngine._init_clients() so voice_ws can find CARTESIA_API_KEY
    even when the server wasn't started with keys in the shell environment.
    """
    from pathlib import Path

    _keys_file = Path.home() / ".openjarvis" / "cloud-keys.env"
    if _keys_file.exists():
        for raw in _keys_file.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v and not os.environ.get(k):
                    os.environ[k] = v

# Sentence boundary pattern — same as existing frontend logic
_SENTENCE_RE = re.compile(r"^([\s\S]*?[.!?])\s+([\s\S]*)$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tools_openai() -> List[Dict[str, Any]]:
    """Get registered tools in OpenAI format."""
    import openjarvis.tools  # noqa: F401
    from openjarvis.core.registry import ToolRegistry

    tools: List[Dict[str, Any]] = []
    for _key, tool_cls in ToolRegistry.items():
        try:
            instance = tool_cls() if callable(tool_cls) else tool_cls
            spec = instance.spec if hasattr(instance, "spec") else None
            if spec is None:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters or {"type": "object", "properties": {}},
                },
            })
        except Exception:
            continue
    return tools


async def _send_json(ws: WebSocket, data: dict) -> None:
    """Send a JSON message, ignoring closed connections."""
    try:
        await ws.send_text(json.dumps(data))
    except Exception:
        pass


async def _send_audio(ws: WebSocket, pcm_bytes: bytes) -> None:
    """Send binary audio, ignoring closed connections."""
    try:
        await ws.send_bytes(pcm_bytes)
    except Exception:
        pass


def _transcribe_sync(speech_backend, pcm_bytes: bytes) -> str:
    """Run transcription in a thread (it's CPU-bound)."""
    # Write PCM to a temporary WAV file for faster-whisper
    import wave

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(16000)
            wf.writeframes(pcm_bytes)

    try:
        result = speech_backend.transcribe(
            open(tmp_path, "rb").read(), format="wav"
        )
        return result.text.strip() if result.text else ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _openai_tts_sync(text: str, voice: str, api_key: str) -> bytes:
    """Synthesize text via OpenAI TTS.

    Returns raw PCM float32 at 24kHz mono so the frontend playback path
    works unchanged.
    """
    import httpx
    import struct

    resp = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "pcm",
            "speed": 1.0,
        },
        timeout=30.0,
    )
    resp.raise_for_status()

    # OpenAI pcm returns s16le @ 24kHz — convert to float32
    s16_data = resp.content
    n_samples = len(s16_data) // 2
    samples = struct.unpack(f"<{n_samples}h", s16_data)
    float_samples = struct.pack(f"<{n_samples}f", *(s / 32768.0 for s in samples))
    return float_samples


# ---------------------------------------------------------------------------
# Voice session
# ---------------------------------------------------------------------------


class VoiceSession:
    """Manages one voice WebSocket session."""

    def __init__(
        self,
        ws: WebSocket,
        *,
        engine: Any,
        model: str,
        voice_id: str,
        speech_backend: Any,
        app_state: Any,
    ):
        self.ws = ws
        self.engine = engine
        self.model = model
        self.voice_id = voice_id
        self.speech_backend = speech_backend
        self.app_state = app_state

        self.messages: List[Message] = []
        self._cancelled = False

        # Ensure cloud-keys.env is loaded (mirrors CloudEngine pattern)
        _ensure_cloud_keys_loaded()
        self._openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not self._openai_key:
            logger.warning("OPENAI_API_KEY not found — TTS will be disabled")

    def _build_system_prompt(self) -> str:
        """Build system prompt matching the chat path, with voice-specific guidance."""
        voice_preamble = (
            "You are Jarvis, a voice assistant. You are responding via speech.\n\n"
            "CRITICAL RULES FOR VOICE MODE:\n"
            "- Answer simple questions DIRECTLY. Do NOT use tools for questions you "
            "already know the answer to (dates, math, facts, definitions, opinions).\n"
            "- Keep responses concise and conversational — the user is listening, not reading.\n"
            "- Only use tools when the user explicitly asks you to DO something "
            "(create a reminder, send a message, search the web, look something up "
            "that requires real-time data, etc.).\n"
            "- Never narrate your tool usage. Just do it and report the result.\n"
        )

        # Try to load the same persona/memory the chat path uses
        try:
            from openjarvis.prompt.builder import SystemPromptBuilder

            config = self.app_state.config
            if config is not None:
                agent_cfg = getattr(config, "agent", None)
                agent_template = ""
                if agent_cfg:
                    agent_template = (
                        agent_cfg.system_prompt
                        or agent_cfg.default_system_prompt
                    )
                builder = SystemPromptBuilder(
                    agent_template=agent_template,
                    memory_files_config=getattr(config, "memory_files", None),
                    system_prompt_config=getattr(config, "system_prompt", None),
                )
                persona = builder.build()
                if persona and persona.strip():
                    return voice_preamble + "\n" + persona.strip()
        except Exception:
            logger.debug("Voice system prompt builder failed", exc_info=True)

        return voice_preamble

    async def run_pipeline(self, speech_pcm: bytes) -> None:
        """Full pipeline: transcribe → LLM (with tools) → TTS → stream audio.

        Designed to run as a background ``asyncio.Task`` so the WebSocket
        receive loop stays responsive.  The caller sets ``_cancelled = False``
        before launching the task and may set it to ``True`` at any time
        (barge-in) to abort mid-pipeline.
        """
        # 1. Transcribe
        await _send_json(self.ws, {"type": "state", "state": "transcribing"})
        text = await asyncio.to_thread(_transcribe_sync, self.speech_backend, speech_pcm)

        if self._cancelled:
            return

        if not text or len(text.strip()) < 2:
            await _send_json(self.ws, {"type": "state", "state": "idle"})
            return

        await _send_json(self.ws, {
            "type": "transcript", "text": text, "is_final": True,
        })

        # 2. LLM inference with tool loop
        await _send_json(self.ws, {"type": "state", "state": "thinking"})

        # Pre-check provider cooldown — skip straight to fallback if provider
        # is known to be down (avoids wasting a round-trip on every request).
        from openjarvis.server.cloud_router import resolve_model_with_fallback

        effective_model, original = resolve_model_with_fallback(self.model)
        if original:
            logger.info("Voice: provider cooldown, using %s instead of %s", effective_model, original)
            self.model = effective_model
            await _send_json(self.ws, {
                "type": "llm.delta",
                "content": f"*Using {effective_model} — {original} temporarily unavailable.*\n\n",
            })

        # Inject system prompt on first user message
        if not self.messages:
            sys_prompt = self._build_system_prompt()
            self.messages.append(Message(role=Role.SYSTEM, content=sys_prompt))

        self.messages.append(Message(role=Role.USER, content=text))

        tools = _get_tools_openai()
        full_response = await self._llm_with_tools(tools)

        if full_response:
            self.messages.append(Message(role=Role.ASSISTANT, content=full_response))
        else:
            self.messages.append(Message(role=Role.ASSISTANT, content="(no response)"))

        # Only send final state if not cancelled (barge-in already moved
        # state to "listening" — sending "idle" here would clobber it).
        if not self._cancelled:
            await _send_json(self.ws, {
                "type": "llm.done", "content": full_response or "",
            })
            await _send_json(self.ws, {"type": "state", "state": "idle"})

    async def _llm_with_tools(self, tools: list) -> str:
        """Run LLM with tool execution loop, streaming text and TTS."""
        from openjarvis.core.registry import ToolRegistry

        messages = list(self.messages)
        accumulated = ""
        sentence_buffer = ""
        MAX_TURNS = 5

        for _turn in range(MAX_TURNS):
            if self._cancelled:
                break

            turn_content = ""
            tool_call_fragments: Dict[int, Dict[str, Any]] = {}
            finish_reason = "stop"

            try:
                async for sc in self.engine.stream_full(
                    messages,
                    model=self.model,
                    tools=tools,
                ):
                    if self._cancelled:
                        break
                    if sc.content:
                        turn_content += sc.content
                        accumulated += sc.content

                        # Stream text delta to client
                        await _send_json(self.ws, {
                            "type": "llm.delta", "content": sc.content,
                        })

                        # Buffer sentences for TTS
                        sentence_buffer += sc.content
                        match = _SENTENCE_RE.match(sentence_buffer)
                        if match:
                            sentence = match.group(1).strip()
                            sentence_buffer = match.group(2)
                            if sentence and not self._cancelled:
                                await self._speak(sentence)

                    if sc.tool_calls:
                        for tc in sc.tool_calls:
                            idx = tc.get("index", 0)
                            if idx not in tool_call_fragments:
                                tool_call_fragments[idx] = {
                                    "id": tc.get("id", ""),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            frag = tool_call_fragments[idx]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                frag["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                frag["function"]["arguments"] += fn["arguments"]
                            if tc.get("id"):
                                frag["id"] = tc["id"]
                    if sc.finish_reason:
                        finish_reason = sc.finish_reason

            except Exception as exc:
                # ── Automatic provider fallback on billing/auth errors ──
                from openjarvis.server.cloud_router import (
                    get_fallback_model,
                    get_provider,
                    is_billing_or_auth_error,
                    mark_provider_down,
                )

                if _turn == 0 and not turn_content and is_billing_or_auth_error(exc):
                    # Mark this provider as down so future requests skip it
                    provider = get_provider(self.model)
                    if provider:
                        mark_provider_down(provider)

                    fallback = get_fallback_model(self.model)
                    if fallback:
                        old_model = self.model
                        self.model = fallback
                        logger.warning(
                            "Voice: %s billing/auth error, falling back to %s",
                            old_model, fallback,
                        )
                        await _send_json(self.ws, {
                            "type": "llm.delta",
                            "content": f"*Switched to {fallback} — {old_model} unavailable.*\n\n",
                        })
                        accumulated += f"*Switched to {fallback} — {old_model} unavailable.*\n\n"
                        continue  # Retry this turn with the fallback model

                logger.error("Voice LLM error: %s", exc, exc_info=True)
                await _send_json(self.ws, {"type": "error", "detail": str(exc)})
                break

            # No tool calls → done
            if not tool_call_fragments:
                # Flush remaining sentence buffer
                remaining = sentence_buffer.strip()
                if remaining and not self._cancelled:
                    await self._speak(remaining)
                    sentence_buffer = ""
                break

            # Execute tools
            sorted_tcs = [tool_call_fragments[i] for i in sorted(tool_call_fragments.keys())]

            from openjarvis.core.types import ToolCall as MsgToolCall

            messages.append(Message(
                role=Role.ASSISTANT,
                content=turn_content or None,
                tool_calls=[
                    MsgToolCall(id=tc["id"], name=tc["function"]["name"], arguments=tc["function"]["arguments"])
                    for tc in sorted_tcs
                ],
            ))

            for tc in sorted_tcs:
                if self._cancelled:
                    break
                tool_name = tc["function"]["name"]
                tool_args = tc["function"]["arguments"]
                result_content = f"Tool '{tool_name}' not available"
                succeeded = False

                await _send_json(self.ws, {
                    "type": "tool.start", "tool": tool_name, "arguments": tool_args,
                })

                t0 = time.monotonic()
                try:
                    tool_cls = ToolRegistry.get(tool_name)
                    if tool_cls is not None:
                        from openjarvis.server.agent_manager_routes import _instantiate_managed_tool
                        tool_instance = _instantiate_managed_tool(
                            tool_cls, tool_name,
                            engine=self.engine, model=self.model, app_state=self.app_state,
                        )
                        parsed = json.loads(tool_args) if tool_args else {}
                        result = tool_instance.execute(**parsed)
                        result_content = result.content
                        succeeded = True
                except Exception as exc:
                    logger.error("Voice tool error %s: %s", tool_name, exc, exc_info=True)
                    result_content = f"Error: {exc}"

                latency = (time.monotonic() - t0) * 1000
                await _send_json(self.ws, {
                    "type": "tool.end", "tool": tool_name,
                    "success": succeeded, "latency": latency,
                })

                messages.append(Message(
                    role=Role.TOOL, content=result_content,
                    tool_call_id=tc["id"], name=tool_name,
                ))

        return accumulated

    async def _speak(self, text: str) -> None:
        """Synthesize and stream TTS audio to client."""
        if self._cancelled:
            return
        if not self._openai_key:
            logger.warning("Skipping TTS — no OPENAI_API_KEY")
            await _send_json(self.ws, {
                "type": "error",
                "detail": "TTS disabled — OPENAI_API_KEY not configured",
            })
            return

        # Clean markdown artifacts
        clean = re.sub(r"```[\s\S]*?```", "code block omitted", text)
        clean = re.sub(r"[#*_~`>\[\]]", "", clean)
        clean = re.sub(r"\n+", " ", clean).strip()
        if not clean:
            return

        await _send_json(self.ws, {"type": "tts.start"})

        # Default to OpenAI fable voice (British accent)
        voice = self.voice_id or "fable"

        try:
            pcm = await asyncio.to_thread(
                _openai_tts_sync, clean, voice, self._openai_key,
            )
            if pcm and not self._cancelled:
                # Send in ~100ms chunks (24kHz * 4 bytes * 0.1s = 9600 bytes)
                chunk_size = 9600
                for i in range(0, len(pcm), chunk_size):
                    if self._cancelled:
                        break
                    await _send_audio(self.ws, pcm[i:i + chunk_size])
                    # Tiny yield so the receive loop can process barge-ins
                    await asyncio.sleep(0)
            elif not pcm:
                logger.warning("TTS returned empty audio for: %s", clean[:80])
        except Exception as exc:
            logger.error("Voice TTS error: %s", exc, exc_info=True)
            await _send_json(self.ws, {
                "type": "error", "detail": f"TTS failed: {exc}",
            })

        if not self._cancelled:
            await _send_json(self.ws, {"type": "tts.done"})

    def cancel(self) -> None:
        """Cancel in-progress pipeline."""
        self._cancelled = True


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


async def _cancel_pipeline(
    session: VoiceSession,
    task: "asyncio.Task[None] | None",
) -> None:
    """Cancel a running pipeline and wait briefly for it to stop."""
    if task is None or task.done():
        return
    session.cancel()
    # Give the pipeline up to 0.5 s to notice _cancelled
    for _ in range(5):
        if task.done():
            break
        await asyncio.sleep(0.1)


@router.websocket("/v1/voice/stream")
async def voice_stream(websocket: WebSocket):
    """WebSocket voice streaming endpoint.

    The receive loop runs continuously so it can process incoming audio
    (VAD) even while a pipeline task is generating LLM + TTS output.
    This enables barge-in: if the user starts speaking during generation,
    the current pipeline is cancelled and a new one starts.
    """
    await websocket.accept()

    app_state = websocket.app.state
    engine = getattr(app_state, "engine", None)
    speech_backend = getattr(app_state, "speech_backend", None)

    if engine is None or speech_backend is None:
        await _send_json(websocket, {
            "type": "error", "detail": "Engine or speech backend not available",
        })
        await websocket.close()
        return

    session: VoiceSession | None = None
    vad = None
    pipeline_task: asyncio.Task[None] | None = None

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            # Text message — JSON control
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "session.start":
                    config = data.get("config", {})
                    model = config.get("model", "claude-sonnet-4-20250514")
                    voice_id = config.get(
                        "voice_id", "JBFqnCBsd6RMkjVDRZzb"
                    )

                    # Initialize VAD
                    try:
                        from openjarvis.speech.silero_vad import SileroVAD
                        vad = SileroVAD(
                            threshold=0.5,
                            min_speech_ms=250,
                            min_silence_ms=800,
                            speech_pad_ms=300,
                        )
                    except Exception as exc:
                        await _send_json(websocket, {
                            "type": "error", "detail": f"VAD init failed: {exc}",
                        })
                        break

                    session = VoiceSession(
                        websocket,
                        engine=engine,
                        model=model,
                        voice_id=voice_id,
                        speech_backend=speech_backend,
                        app_state=app_state,
                    )
                    await _send_json(websocket, {"type": "session.started"})

                elif msg_type == "interrupt" and session:
                    # Manual interrupt from frontend
                    await _cancel_pipeline(session, pipeline_task)
                    await _send_json(websocket, {"type": "stop_playback"})
                    if vad:
                        vad.reset()
                    await _send_json(websocket, {"type": "state", "state": "idle"})

                elif msg_type == "commit" and session and vad:
                    event = vad.force_end()
                    if event.type == "speech_end" and event.speech_audio:
                        await _cancel_pipeline(session, pipeline_task)
                        session._cancelled = False
                        pipeline_task = asyncio.create_task(
                            session.run_pipeline(event.speech_audio)
                        )

                elif msg_type == "session.end":
                    break

            # Binary message — PCM audio from mic
            elif "bytes" in msg and vad and session:
                pcm_data = msg["bytes"]

                from openjarvis.speech.silero_vad import CHUNK_BYTES

                offset = 0
                while offset + CHUNK_BYTES <= len(pcm_data):
                    chunk = pcm_data[offset:offset + CHUNK_BYTES]
                    offset += CHUNK_BYTES

                    event = vad.process_chunk(chunk)

                    if event.type == "speech_start":
                        # ── Barge-in: cancel running pipeline ──
                        if pipeline_task and not pipeline_task.done():
                            session.cancel()
                            await _send_json(websocket, {"type": "stop_playback"})
                        await _send_json(websocket, {"type": "vad.speech_start"})
                        await _send_json(websocket, {"type": "state", "state": "listening"})

                    elif event.type == "speech_end" and event.speech_audio:
                        await _send_json(websocket, {"type": "vad.speech_end"})
                        # Cancel previous pipeline if it's still running
                        await _cancel_pipeline(session, pipeline_task)
                        # Launch new pipeline as background task so the
                        # receive loop stays responsive for further barge-ins
                        session._cancelled = False
                        pipeline_task = asyncio.create_task(
                            session.run_pipeline(event.speech_audio)
                        )

    except WebSocketDisconnect:
        logger.debug("Voice WebSocket disconnected")
    except Exception as exc:
        logger.error("Voice WebSocket error: %s", exc, exc_info=True)
    finally:
        if session:
            session.cancel()
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@router.get("/v1/voice/health")
async def voice_health(request: Request):
    """Check if WebSocket voice streaming is available."""
    speech_ok = getattr(request.app.state, "speech_backend", None) is not None
    try:
        from openjarvis.speech.silero_vad import silero_available
        vad_ok = silero_available()
    except ImportError:
        vad_ok = False
    return {"available": speech_ok and vad_ok, "vad": vad_ok, "stt": speech_ok}
