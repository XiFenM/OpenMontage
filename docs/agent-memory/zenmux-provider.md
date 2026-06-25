---
name: zenmux-provider
description: ZenMux provider tools added to OpenMontage — verified model ids and API quirks
scope: repo
metadata: 
  node_type: memory
  type: project
  originSessionId: 96982a05-1815-4255-9163-643db0afe646
---

User is a ZenMux (https://zenmux.ai) member and uses it as the aggregator for image/video/speech generation (no music). Added 3 provider tools (all gated on `ZENMUX_API_KEY`, all via `requests`):
- `tools/graphics/zenmux_image.py` (ZenMuxImage) — OpenAI-compat `POST /api/v1/images/generations`, returns b64_json
- `tools/video/zenmux_video.py` (ZenMuxVideo) — Vertex AI async `:predictLongRunning` + poll `:fetchPredictOperation`
- `tools/audio/zenmux_speech.py` (ZenMuxSpeech) — Vertex `:generateContent` with responseModalities=["AUDIO"] (Gemini TTS); wraps returned PCM into WAV

**Verified working model ids (live-tested 2026-06):**
- image: `gpt-image-2` (also `openai/gpt-image-2`, `openai/gpt-image-1.5`)
- speech: `google/gemini-3.1-flash-tts-preview` (voice e.g. Kore)
- video: `bytedance/doubao-seedance-2.0` (user's choice, native audio) and `google/veo-3.1-generate-001`

**Gotchas (cost real money to discover — don't relearn):**
- The ZenMux *docs* list seedance ids `volcengine/doubao-seedance-2` and `volcengine/doubao-seedance-1.5-pro` — these return 404 `invalid_model`. Real id is `bytedance/doubao-seedance-2.0`.
- Veo `google/veo-3.1-generate-001` only accepts durationSeconds in [4,6,8]; 5 errors out. Seedance accepts 5.
- The list endpoints (`/api/v1/models`, `/api/vertex-ai/v1beta/models`) do NOT enumerate video/speech-output models reliably — only the website's modality-filtered pages do. Validate an id with a submit probe (404 = no charge).
- Speech returns raw PCM (audio/L16) → must wrap in WAV. Video response `videos[0]` carries both `gcsUri` and `bytesBase64Encoded`.

**Size/duration limits (now validated pre-flight in the tools):**
- Image `gpt-image-2`: arbitrary WxH, both sides divisible by 16, aspect ratio 1:3–3:1, max 3840x2160 (>2560x1440 warns experimental). `gpt-image-1.5`: only 1024x1024 / 1536x1024 / 1024x1536 / auto. n: 1–10. See `validate_image_size()`.
- Video: Veo durations only {4,6,8}; Seedance 1–10. resolution {720p,1080p}, aspect {16:9,9:16,1:1}. See `validate_video_params()`.

See [[env-setup-uv]] for running the repo.
