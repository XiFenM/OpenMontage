"""ZenMux video generation (Vertex AI protocol aggregator).

ZenMux (https://zenmux.ai) exposes video generation models (Google Veo,
ByteDance Seedance, ...) through the Google Vertex AI protocol at
https://zenmux.ai/api/vertex-ai. A single ZENMUX_API_KEY unlocks all of them.

Generation is asynchronous:
  1. POST .../publishers/{provider}/models/{model}:predictLongRunning  -> {"name": op}
  2. POST .../publishers/{provider}/models/{model}:fetchPredictOperation -> poll until done
  3. response.videos[] carries gcsUri or bytesBase64Encoded

Seedance and Veo can generate a synchronized audio track in the same pass
(``generateAudio: true``), so no separate narration/SFX step is required.

Docs: https://zenmux.ai/docs/api/vertexai/generate-videos.html
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
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

# Per-model duration limits (seconds). Veo only accepts a fixed set; Seedance
# accepts a continuous range up to 10s. Models not listed fall through to the
# schema's 1-10 bound and let the API enforce its own limit.
VEO_DURATIONS = {4, 6, 8}
SEEDANCE_MAX_DURATION = 10


def validate_video_params(model_id: str, duration: int) -> str | None:
    """Return an error string if duration is invalid for the model, else None.

    Catches the common foot-gun (Veo rejecting 5s) before spending an API call.
    """
    model = model_id.lower()
    if "veo" in model and duration not in VEO_DURATIONS:
        return (
            f"Veo only supports durations {sorted(VEO_DURATIONS)} seconds; got {duration}. "
            f"Try {min(VEO_DURATIONS, key=lambda d: abs(d - duration))}."
        )
    if "seedance" in model and not 1 <= duration <= SEEDANCE_MAX_DURATION:
        return f"Seedance supports 1-{SEEDANCE_MAX_DURATION} seconds; got {duration}."
    return None


def _split_model(model_id: str) -> tuple[str, str]:
    """Split a 'provider/model' id into (provider, model) for the URL path."""
    if "/" not in model_id:
        raise ValueError(
            f"ZenMux video model id must be in 'provider/model' form, got {model_id!r}. "
            "Example: volcengine/doubao-seedance-2"
        )
    provider, model = model_id.split("/", 1)
    return provider, model


def _image_blob(path_value: str | None) -> dict[str, str] | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Input image not found: {path}")
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"bytesBase64Encoded": encoded, "mimeType": mime_type}


def _find_videos(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the generated-video list out of a completed operation, tolerantly."""
    response = operation.get("response") or {}
    for key in ("videos", "generated_videos", "generatedVideos"):
        videos = response.get(key)
        if isinstance(videos, list) and videos:
            return videos
    # Some Vertex responses nest under generateVideoResponse.generatedSamples
    nested = response.get("generateVideoResponse") or {}
    samples = nested.get("generatedSamples") or nested.get("videos")
    if isinstance(samples, list) and samples:
        return samples
    return []


class ZenMuxVideo(BaseTool):
    name = "zenmux_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
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
    agent_skills = ["seedance-2-0", "ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "native_audio": True,
        "cinematic_quality": True,
        "aggregator": True,
    }
    best_for = [
        "single key access to Veo / Seedance video models",
        "clips with synchronized native audio in one pass (Seedance, Veo)",
        "cinematic motion shots from a text prompt or a start frame",
    ]
    not_good_for = ["offline generation", "clips longer than ~10s per call"]
    fallback_tools = ["seedance_video", "veo_video", "kling_video", "grok_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "default": "bytedance/doubao-seedance-2.0",
                "description": (
                    "ZenMux video model id in 'provider/model' form. Examples: "
                    "bytedance/doubao-seedance-2.0 (native audio), "
                    "google/veo-3.1-generate-001 (durations 4/6/8 only). Browse "
                    "https://zenmux.ai/models?output_modalities=video for the full list."
                ),
            },
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video"],
                "default": "text_to_video",
            },
            "duration": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": (
                    "Clip length in seconds. Per-model limits: Veo accepts only 4, 6, or 8; "
                    "Seedance accepts 1-10. Validated before the API call."
                ),
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1"],
                "default": "16:9",
            },
            "resolution": {
                "type": "string",
                "enum": ["720p", "1080p"],
                "default": "720p",
            },
            "generate_audio": {
                "type": "boolean",
                "default": True,
                "description": "Generate a synchronized audio track (Seedance/Veo support this).",
            },
            "negative_prompt": {"type": "string"},
            "seed": {"type": "integer"},
            "image_path": {"type": "string", "description": "Start-frame image for image_to_video"},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "integer", "minimum": 5, "default": 15},
            "timeout_seconds": {"type": "integer", "minimum": 60, "default": 600},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "operation", "model", "duration", "aspect_ratio", "resolution", "seed"]
    side_effects = ["writes video file to output_path", "calls ZenMux video API"]
    user_visible_verification = ["Watch generated clip for motion quality and prompt fidelity"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("ZENMUX_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Coarse per-second estimate so the cost tracker can reserve budget.
        # Actual price depends on the underlying model (Veo > Seedance).
        duration = int(inputs.get("duration", 5))
        per_second = 0.15 if "veo" in inputs.get("model", "") else 0.10
        return round(per_second * duration, 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 60.0 + int(inputs.get("duration", 5)) * 10.0

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        instance: dict[str, Any] = {"prompt": inputs["prompt"]}
        if inputs.get("operation") == "image_to_video":
            blob = _image_blob(inputs.get("image_path"))
            if not blob:
                raise ValueError("image_to_video requires image_path")
            instance["image"] = blob

        parameters: dict[str, Any] = {
            "aspectRatio": inputs.get("aspect_ratio", "16:9"),
            "durationSeconds": int(inputs.get("duration", 5)),
            "resolution": inputs.get("resolution", "720p"),
            "generateAudio": bool(inputs.get("generate_audio", True)),
        }
        if inputs.get("negative_prompt"):
            parameters["negativePrompt"] = inputs["negative_prompt"]
        if inputs.get("seed") is not None:
            parameters["seed"] = int(inputs["seed"])

        return {"instances": [instance], "parameters": parameters}

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("ZENMUX_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="ZENMUX_API_KEY not set. " + self.install_instructions,
            )

        import requests
        from tools.video._shared import probe_output

        start = time.time()
        model_id = inputs.get("model", "bytedance/doubao-seedance-2.0")
        try:
            provider, model = _split_model(model_id)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        duration_error = validate_video_params(model_id, int(inputs.get("duration", 5)))
        if duration_error:
            return ToolResult(success=False, error=duration_error)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        base = f"{ZENMUX_VERTEX_BASE}/publishers/{provider}/models/{model}"

        try:
            payload = self._build_payload(inputs)
            submit = requests.post(
                f"{base}:predictLongRunning", headers=headers, json=payload, timeout=60
            )
            submit.raise_for_status()
            op_name = submit.json().get("name")
            if not op_name:
                return ToolResult(success=False, error=f"ZenMux did not return an operation name: {submit.json()}")

            timeout_seconds = int(inputs.get("timeout_seconds", 600))
            poll_interval = int(inputs.get("poll_interval_seconds", 15))
            deadline = time.time() + timeout_seconds

            operation: dict[str, Any] | None = None
            while time.time() < deadline:
                time.sleep(poll_interval)
                poll = requests.post(
                    f"{base}:fetchPredictOperation",
                    headers=headers,
                    json={"operationName": op_name},
                    timeout=60,
                )
                poll.raise_for_status()
                operation = poll.json()
                if operation.get("done"):
                    break

            if not operation or not operation.get("done"):
                return ToolResult(success=False, error="ZenMux video generation timed out")

            if operation.get("error"):
                return ToolResult(success=False, error=f"ZenMux video generation failed: {operation['error']}")

            response = operation.get("response") or {}
            filtered = response.get("raiMediaFilteredCount") or 0
            videos = _find_videos(operation)
            if not videos:
                reasons = response.get("raiMediaFilteredReasons")
                if filtered:
                    return ToolResult(
                        success=False,
                        error=f"ZenMux blocked the video by content moderation: {reasons}",
                    )
                return ToolResult(success=False, error=f"ZenMux returned no video: {operation}")

            video = videos[0]
            # Vertex SDK objects sometimes nest the payload under a 'video' key.
            if "video" in video and isinstance(video["video"], dict):
                video = video["video"]

            output_path = Path(inputs.get("output_path", "zenmux_video.mp4"))
            output_path.parent.mkdir(parents=True, exist_ok=True)

            b64 = video.get("bytesBase64Encoded")
            uri = video.get("gcsUri") or video.get("uri") or video.get("url")
            if b64:
                output_path.write_bytes(base64.b64decode(b64))
            elif uri:
                dl = requests.get(uri, headers={"Authorization": headers["Authorization"]}, timeout=300)
                if dl.status_code in (401, 403):
                    dl = requests.get(uri, timeout=300)  # signed URL: no auth header
                dl.raise_for_status()
                output_path.write_bytes(dl.content)
            else:
                return ToolResult(success=False, error=f"ZenMux video output missing data/uri: {video}")

        except Exception as e:
            return ToolResult(success=False, error=f"ZenMux video generation failed: {e}")

        probed = probe_output(output_path)
        return ToolResult(
            success=True,
            data={
                "provider": "zenmux",
                "model": model_id,
                "prompt": inputs["prompt"],
                "operation": inputs.get("operation", "text_to_video"),
                "generate_audio": bool(inputs.get("generate_audio", True)),
                "output": str(output_path),
                "output_path": str(output_path),
                "format": "mp4",
                **probed,
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model_id,
        )
