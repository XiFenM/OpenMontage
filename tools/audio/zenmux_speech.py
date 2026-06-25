"""ZenMux text-to-speech (Gemini TTS via Vertex AI protocol).

ZenMux (https://zenmux.ai) exposes speech/TTS models through the Google Vertex
AI ``generateContent`` endpoint with ``responseModalities: ["AUDIO"]``. A single
ZENMUX_API_KEY unlocks them. The model returns raw PCM audio inline (base64),
which this tool wraps into a playable WAV file.

Docs: https://zenmux.ai/docs/api/vertexai/generate-content.html
"""

from __future__ import annotations

import base64
import io
import os
import re
import time
import wave
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

ZENMUX_VERTEX_BASE = "https://zenmux.ai/api/vertex-ai/v1"

# Gemini TTS prebuilt voices (a representative subset — many more exist).
PREBUILT_VOICES = ["Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr"]


def _split_model(model_id: str) -> tuple[str, str]:
    if "/" not in model_id:
        raise ValueError(
            f"ZenMux speech model id must be in 'provider/model' form, got {model_id!r}. "
            "Example: google/gemini-2.5-flash-preview-tts"
        )
    provider, model = model_id.split("/", 1)
    return provider, model


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit little-endian mono PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return buf.getvalue()


def _sample_rate_from_mime(mime_type: str) -> int:
    match = re.search(r"rate=(\d+)", mime_type or "")
    return int(match.group(1)) if match else 24000


class ZenMuxSpeech(BaseTool):
    name = "zenmux_speech"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "zenmux"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:ZENMUX_API_KEY"]
    install_instructions = (
        "Set ZENMUX_API_KEY to your ZenMux API key.\n"
        "  Get one at https://zenmux.ai (Settings -> API Keys)\n"
        "One key unlocks image, video, and speech models across many providers."
    )
    fallback = "piper_tts"
    fallback_tools = ["elevenlabs_tts", "openai_tts", "piper_tts"]
    agent_skills = ["elevenlabs"]  # general TTS direction/prompting knowledge

    capabilities = ["text_to_speech", "voice_selection"]
    supports = {
        "voice_cloning": True,  # replicatedVoiceConfig from an audio sample
        "multilingual": True,
        "offline": False,
        "native_audio": True,
        "aggregator": True,
    }
    best_for = [
        "single key access to Gemini TTS voices",
        "expressive narration via natural-language delivery instructions in the prompt",
    ]
    not_good_for = ["fully offline production"]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "model": {
                "type": "string",
                "default": "google/gemini-3.1-flash-tts-preview",
                "description": (
                    "ZenMux speech model id in 'provider/model' form. Browse "
                    "https://zenmux.ai/models?output_modalities=speech for the full list."
                ),
            },
            "voice": {
                "type": "string",
                "default": "Kore",
                "description": f"Prebuilt voice name, e.g. {', '.join(PREBUILT_VOICES)}",
            },
            "language_code": {
                "type": "string",
                "default": "en-US",
                "description": "BCP-47 language code for speech output, e.g. en-US, zh-CN",
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["text", "voice", "model", "language_code"]
    side_effects = ["writes audio file to output_path", "calls ZenMux speech API"]
    user_visible_verification = ["Listen to generated audio for intelligibility and tone"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("ZENMUX_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Gemini TTS bills per character/token; coarse estimate for budgeting.
        return round(len(inputs.get("text", "")) * 0.00002, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("ZENMUX_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="ZENMUX_API_KEY not set. " + self.install_instructions,
            )

        import requests

        start = time.time()
        model_id = inputs.get("model", "google/gemini-3.1-flash-tts-preview")
        try:
            provider, model = _split_model(model_id)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        text = inputs["text"]
        voice = inputs.get("voice", "Kore")
        language_code = inputs.get("language_code", "en-US")

        payload = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                    "languageCode": language_code,
                },
            },
        }

        try:
            response = requests.post(
                f"{ZENMUX_VERTEX_BASE}/publishers/{provider}/models/{model}:generateContent",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=180,
            )
            response.raise_for_status()
            body = response.json()

            inline = self._extract_inline_audio(body)
            if not inline:
                return ToolResult(success=False, error=f"ZenMux returned no audio: {body}")

            mime_type = inline.get("mimeType", "")
            audio_bytes = base64.b64decode(inline["data"])

            output_path = Path(inputs.get("output_path", "zenmux_speech.wav"))
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Gemini TTS returns raw little-endian PCM (audio/L16 or audio/pcm);
            # wrap it in a WAV container so it is directly playable.
            if "wav" in mime_type or "mpeg" in mime_type or "mp3" in mime_type:
                output_path.write_bytes(audio_bytes)
            else:
                sample_rate = _sample_rate_from_mime(mime_type)
                if output_path.suffix.lower() != ".wav":
                    output_path = output_path.with_suffix(".wav")
                output_path.write_bytes(_pcm_to_wav(audio_bytes, sample_rate))

        except Exception as e:
            return ToolResult(success=False, error=f"ZenMux speech generation failed: {e}")

        audio_duration = None
        try:
            from tools.analysis.audio_probe import probe_duration

            audio_duration = probe_duration(output_path)
        except Exception:
            pass

        return ToolResult(
            success=True,
            data={
                "provider": "zenmux",
                "model": model_id,
                "voice": voice,
                "language_code": language_code,
                "text_length": len(text),
                "audio_duration_seconds": round(audio_duration, 2) if audio_duration else None,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model_id,
        )

    @staticmethod
    def _extract_inline_audio(body: dict[str, Any]) -> dict[str, str] | None:
        """Find the first inlineData audio blob in a generateContent response."""
        for candidate in body.get("candidates") or []:
            parts = ((candidate.get("content") or {}).get("parts")) or []
            for part in parts:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return {
                        "data": inline["data"],
                        "mimeType": inline.get("mimeType") or inline.get("mime_type") or "",
                    }
        return None
