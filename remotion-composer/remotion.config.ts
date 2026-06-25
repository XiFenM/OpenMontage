import { Config } from "@remotion/cli/config";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

/**
 * Resolve a working Chrome/Chromium for rendering.
 *
 * Remotion normally downloads its own Chrome Headless Shell. In some slim
 * containers that bundled binary's version probe hangs, so Remotion decides the
 * install "differs", re-downloads every run, and the re-extraction gets
 * interrupted — leaving a partial install and a render that exits 0 with NO
 * output file. To avoid that loop we point Remotion at a known-good browser:
 *
 *   1. REMOTION_BROWSER_EXECUTABLE env var, if it points at a real file.
 *   2. A Playwright-installed chrome-headless-shell / chromium, if present.
 *   3. Otherwise do nothing — Remotion uses its own bundled download.
 *
 * Steps 1-2 are no-ops on machines without those binaries, so this stays
 * portable (CI / other dev machines fall through to Remotion's default).
 */
function resolveBrowserExecutable(): string | null {
  const fromEnv = process.env.REMOTION_BROWSER_EXECUTABLE;
  if (fromEnv && existsSync(fromEnv)) {
    return fromEnv;
  }

  const pwRoot = join(process.env.HOME ?? "/root", ".cache", "ms-playwright");
  try {
    for (const entry of readdirSync(pwRoot)) {
      const candidates = [
        join(pwRoot, entry, "chrome-headless-shell-linux64", "chrome-headless-shell"),
        join(pwRoot, entry, "chrome-linux", "chrome"),
        join(pwRoot, entry, "chrome-linux64", "chrome"),
      ];
      for (const bin of candidates) {
        if (existsSync(bin)) {
          return bin;
        }
      }
    }
  } catch {
    // ms-playwright dir absent — fall through to Remotion's bundled Chrome.
  }
  return null;
}

const browserExecutable = resolveBrowserExecutable();
if (browserExecutable) {
  Config.setBrowserExecutable(browserExecutable);
}
