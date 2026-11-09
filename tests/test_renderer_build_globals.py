"""Every build-time global the renderer reads must be defined by every config that serves it.

The smoke/crawler server has its own Vite config; a global added to the app but not
there throws a ReferenceError that only CI's route smokes and UI crawler see.
"""

import re
from pathlib import Path

ELECTRON = Path(__file__).resolve().parents[1] / "electron"
CONFIGS = ["tests/renderer-smoke.vite.config.mjs", "vite.web.config.ts", "electron.vite.config.ts"]


def test_every_renderer_global_is_defined_where_the_renderer_is_served():
    declared = set(re.findall(r"declare const (__\w+__)", (ELECTRON / "src/renderer/env.d.ts").read_text(encoding="utf-8")))
    assert declared, "no renderer build globals found"
    for config in CONFIGS:
        text = (ELECTRON / config).read_text(encoding="utf-8")
        missing = {name for name in declared if name not in text}
        assert not missing, f"{config} does not define {sorted(missing)}"
