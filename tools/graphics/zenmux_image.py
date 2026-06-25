"""ZenMux image generation (OpenAI-compatible aggregator).

ZenMux (https://zenmux.ai) is a multi-provider model aggregator that exposes an
OpenAI-compatible Images API at https://zenmux.ai/api/v1. A single ZENMUX_API_KEY
unlocks many image models (gpt-image family and others). This tool calls the
``/images/generations`` endpoint via plain HTTP (no SDK dependency), matching the
pattern used by other API provider tools in this repo.

Docs: https://zenmux.ai/docs/api/openai/generate-an-image
"""

from __future__ import annotations

import base64
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

ZENMUX_OPENAI_BASE = "https://zenmux.ai/api/v1"

# Image size limits per ZenMux docs (api/openai/generate-an-image).
STANDARD_SIZES = {"1024x1024", "1536x1024", "1024x1536"}
MAX_PIXELS = 3840 * 2160          # hard max supported resolution
EXPERIMENTAL_PIXELS = 2560 * 1440  # above this is flagged experimental
MAX_SIDE = 3840


def validate_image_size(model: str, size: str) -> tuple[str | None, str | None]:
    """Validate a requested image size against ZenMux per-model limits.

    Returns (error, warning). ``error`` is non-None when the size is invalid
    and the call should be rejected before hitting the API; ``warning`` is a
    non-blocking advisory (e.g. experimental high resolutions).
    """
    if not size or size == "auto":
        return None, None

    # gpt-image-1.5 only accepts the three standard sizes (or auto).
    if "1.5" in model:
        if size not in STANDARD_SIZES:
            return (
                f"{model} only supports sizes {sorted(STANDARD_SIZES)} or 'auto', got {size!r}.",
                None,
            )
        return None, None

    # gpt-image-2 family: arbitrary WIDTHxHEIGHT with constraints.
    import re

    match = re.fullmatch(r"(\d+)x(\d+)", size)
    if not match:
        return (f"Invalid size {size!r}. Use 'WIDTHxHEIGHT' (e.g. 1536x864) or 'auto'.", None)
    width, height = int(match.group(1)), int(match.group(2))

    if width % 16 != 0 or height % 16 != 0:
        return (f"Both width and height must be divisible by 16; got {width}x{height}.", None)
    ratio = width / height
    if not (1 / 3 <= ratio <= 3):
        return (
            f"Aspect ratio must be between 1:3 and 3:1; {width}x{height} is {ratio:.2f}:1.",
            None,
        )
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        return (
            f"Size {width}x{height} exceeds the maximum supported resolution of 3840x2160.",
            None,
        )

    warning = None
    if width * height > EXPERIMENTAL_PIXELS:
        warning = f"Resolution {width}x{height} is above 2560x1440 and is experimental on ZenMux."
    return None, warning


class ZenMuxImage(BaseTool):
    name = "zenmux_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
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
    agent_skills = ["flux-best-practices"]  # general image-gen prompting knowledge

    capabilities = ["generate_image", "generate_illustration", "text_to_image"]
    supports = {
        "complex_instructions": True,
        "text_in_image": True,
        "multiple_outputs": True,
        "aggregator": True,
    }
    best_for = [
        "single key access to many image models via one provider",
        "gpt-image quality compositions with text/labels",
    ]
    not_good_for = ["offline generation"]
    fallback_tools = ["flux_image", "openai_image", "google_imagen"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "default": "gpt-image-2",
                "description": (
                    "ZenMux image model id. Examples: gpt-image-2, gpt-image-1.5. "
                    "Browse https://zenmux.ai/models?output_modalities=image for the full list."
                ),
            },
            "size": {
                "type": "string",
                "default": "1024x1024",
                "description": (
                    "Image size as 'WIDTHxHEIGHT' or 'auto'. Limits (ZenMux docs): "
                    "gpt-image-1.5 only supports 1024x1024 / 1536x1024 / 1024x1536 / auto. "
                    "gpt-image-2 supports arbitrary sizes where both sides are divisible by 16, "
                    "aspect ratio is between 1:3 and 3:1, and max resolution is 3840x2160 "
                    "(>2560x1440 is experimental)."
                ),
            },
            "quality": {
                "type": "string",
                "enum": ["low", "medium", "high", "auto"],
                "default": "high",
            },
            "output_format": {
                "type": "string",
                "enum": ["png", "jpeg", "webp"],
                "default": "png",
            },
            "n": {"type": "integer", "default": 1, "minimum": 1, "maximum": 10},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "size", "quality", "model"]
    side_effects = ["writes image file to output_path", "calls ZenMux API"]
    user_visible_verification = ["Inspect generated image for relevance and quality"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("ZENMUX_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # ZenMux bills per the underlying model; this is a coarse gpt-image-class
        # estimate so the cost tracker has a non-zero figure to reserve against.
        quality = inputs.get("quality", "high")
        n = int(inputs.get("n", 1))
        cost_map = {"low": 0.011, "medium": 0.042, "high": 0.167, "auto": 0.042}
        return round(cost_map.get(quality, 0.042) * n, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("ZENMUX_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="ZENMUX_API_KEY not set. " + self.install_instructions,
            )

        import requests

        start = time.time()
        model = inputs.get("model", "gpt-image-2")
        prompt = inputs["prompt"]
        n = int(inputs.get("n", 1))
        output_format = inputs.get("output_format", "png")
        size = inputs.get("size", "1024x1024")

        if not 1 <= n <= 10:
            return ToolResult(success=False, error=f"n must be between 1 and 10; got {n}.")

        size_error, size_warning = validate_image_size(model, size)
        if size_error:
            return ToolResult(success=False, error=f"Invalid image size: {size_error}")

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": size,
            "quality": inputs.get("quality", "high"),
            "output_format": output_format,
        }

        try:
            response = requests.post(
                f"{ZENMUX_OPENAI_BASE}/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=180,
            )
            response.raise_for_status()
            body = response.json()
            data_items = body.get("data") or []
            if not data_items:
                return ToolResult(success=False, error=f"ZenMux returned no image data: {body}")

            written: list[str] = []
            base_path = Path(inputs.get("output_path", f"zenmux_image.{output_format}"))
            base_path.parent.mkdir(parents=True, exist_ok=True)
            for idx, item in enumerate(data_items):
                b64 = item.get("b64_json")
                if b64:
                    img_bytes = base64.b64decode(b64)
                elif item.get("url"):
                    dl = requests.get(item["url"], timeout=120)
                    dl.raise_for_status()
                    img_bytes = dl.content
                else:
                    continue
                if idx == 0:
                    out = base_path
                else:
                    out = base_path.with_name(f"{base_path.stem}_{idx}{base_path.suffix}")
                out.write_bytes(img_bytes)
                written.append(str(out))

            if not written:
                return ToolResult(success=False, error="ZenMux image response had no decodable images")

        except Exception as e:
            return ToolResult(success=False, error=f"ZenMux image generation failed: {e}")

        return ToolResult(
            success=True,
            data={
                "provider": "zenmux",
                "model": model,
                "prompt": prompt,
                "size": size,
                "output": written[0],
                "outputs": written,
                **({"warning": size_warning} if size_warning else {}),
            },
            artifacts=written,
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
