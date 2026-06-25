---
name: repo-setup-gotchas
description: Cross-environment setup gotchas for running OpenMontage from a clone
metadata: 
  node_type: memory
  scope: repo
  type: project
  originSessionId: 96982a05-1815-4255-9163-643db0afe646
---

Setup facts that apply on any machine running this repo (not machine-specific):

- **`numpy` is required for tool discovery but NOT declared in `requirements.txt`.** `registry.discover()` imports every tool module, and some (e.g. `tools/video/green_screen_composite.py`) `import numpy` at module load, so discovery crashes with `ModuleNotFoundError: numpy` on a clean install. Fix: `pip install numpy` after `requirements.txt`, or add numpy to `requirements.txt` / lazy-import it in those tools.
- **Full capability needs system tools beyond pip:** `ffmpeg` binary (composition/audio/post), Node ≥ 22 + `npx`, and `cd remotion-composer && npm install` (Remotion `node_modules`) for the Remotion/HyperFrames runtimes. Without them the registry reports those composition runtimes unavailable.
- `requirements.txt` ships only core deps (pyyaml, pydantic, jsonschema, python-dotenv, Pillow, requests, google-auth). GPU/extra deps are in `requirements-gpu.txt`.

See [[remotion-render-chrome]] for the Remotion browser fix and [[zenmux-provider]] for the configured providers.
