---
name: remotion-render-chrome
description: Remotion render Chrome issue — FIXED via remotion.config.ts browser auto-detect
scope: repo
metadata: 
  node_type: memory
  type: project
  originSessionId: 96982a05-1815-4255-9163-643db0afe646
---

**Symptom (now fixed):** `npx remotion render` silently failed — exited 0 with NO output file, log stopping right after "Got Headless Shell" (even `--log=verbose`). `video_compose` returned "Remotion render completed but output file missing".

**Root cause:** Remotion's bundled Chrome Headless Shell binary's version probe HANGS in this container (e.g. `chrome-headless-shell --dump-dom about:blank` never returns). So Remotion decides the install "differs", re-downloads every run, and the re-extraction gets interrupted → permanent partial (only `libGLESv2.so` extracted, not the 205MB binary). The downloaded zip itself was valid/complete. Playwright's Chrome works fine here.

**Fix (committed):** `remotion-composer/remotion.config.ts` now calls `Config.setBrowserExecutable(...)`, resolving a working browser in order: (1) `REMOTION_BROWSER_EXECUTABLE` env var, (2) auto-detected Playwright `chrome-headless-shell` under `~/.cache/ms-playwright/`, (3) else Remotion's bundled download. This bypasses the broken bundled-Chrome path entirely. Portable: steps 1-2 are no-ops on machines without those, so CI falls through to default.

Verified: clean `npx remotion still/render` (no flag) AND `video_compose` op `remotion_render` both succeed now. The config auto-detects a Playwright `chrome-headless-shell` under `~/.cache/ms-playwright/`; set `REMOTION_BROWSER_EXECUTABLE` to override. On machines where Remotion's bundled Chrome works, the config is a no-op and falls through to default.

CinematicRenderer mutes clips and plays a separate `soundtrack` track — concat clips' native audio to keep Seedance sound. See [[zenmux-provider]].
