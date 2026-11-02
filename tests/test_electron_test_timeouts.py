"""Release packaging reruns the Electron unit suites on slow shared runners.

v0.5.7-beta.2 and -beta.3 failed when a test that takes ~30 ms on a laptop hit
Vitest's default 5 s timeout there. Both suites must allow a CI-sized timeout.
"""

from pathlib import Path

ELECTRON = Path(__file__).resolve().parents[1] / "electron"


def test_both_vitest_suites_allow_slow_ci_runners():
    for config in ("vite.config.ts", "vite.shared.config.ts"):
        text = (ELECTRON / config).read_text(encoding="utf-8")
        assert "testTimeout: process.env.CI ? 30_000 : 5_000" in text, config
