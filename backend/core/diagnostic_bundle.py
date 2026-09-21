"""Diagnostic bundle — everything a maintainer needs, in one drag-and-drop.

The prefilled GitHub Issues URL caps out around 8k characters, so logs can
never ride along with a report. This module zips the full picture instead:

    omnivoice-diagnostics-<timestamp>.zip
    ├── meta.json            app version, platform, python, generated-at
    ├── self_check.txt       human-readable diagnose report
    ├── self_check.json      same, structured
    ├── errors.json          recent error journal (deduped, classified)
    ├── render_traces.json   recent local render timings and operation counts
    └── logs/
        ├── omnivoice.log.txt    last 500 lines, scrubbed
        └── crash_log.txt        last 200 lines, scrubbed

Settings → About → "Save diagnostic bundle" builds it and reveals the file;
the user drags it onto their GitHub issue. Every text member is passed
through core.scrub — the bundle is built TO leave the machine, so it must
be safe by construction. The zip is written to OUTPUTS_DIR (user-visible,
already revealed-in-folder elsewhere in the app).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import zipfile

from core.config import OUTPUTS_DIR, LOG_PATH, CRASH_LOG_PATH
from core.scrub import scrub_text
from core.version import APP_VERSION

_LOG_TAIL_LINES = 500
_CRASH_TAIL_LINES = 200


def _scrubbed_tail(path: str, max_lines: int) -> str:
    """Last `max_lines` of `path`, scrubbed. Missing/unreadable file → a
    one-line note instead of a hard failure (the bundle must always build)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"(no file at {scrub_text(path)})\n"
    except Exception as e:
        return f"(could not read {scrub_text(path)}: {scrub_text(str(e))})\n"
    return scrub_text("".join(lines[-max_lines:]))


def build_bundle(include_network: bool = False) -> str:
    """Build the zip and return its absolute path.

    ``include_network=False`` by default: the bundle is usually requested
    exactly when something is wrong, and a hung hub probe shouldn't add 5s
    to "save the evidence".
    """
    from core.diagnose import run_diagnostics, format_text
    from core import error_journal, render_trace

    report = run_diagnostics(include_network=include_network)

    meta = {
        "app_version": APP_VERSION,
        "platform": scrub_text(platform.platform()),
        "python": sys.version.split()[0],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUTS_DIR, f"omnivoice-diagnostics-{stamp}.zip")

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
        zf.writestr("self_check.txt", format_text(report))
        zf.writestr("self_check.json", json.dumps(report, indent=2, ensure_ascii=False))
        zf.writestr(
            "errors.json",
            json.dumps(error_journal.recent(50), indent=2, ensure_ascii=False),
        )
        zf.writestr("render_traces.json", json.dumps(render_trace.recent(), indent=2))
        zf.writestr("logs/omnivoice.log.txt", _scrubbed_tail(LOG_PATH, _LOG_TAIL_LINES))
        zf.writestr("logs/crash_log.txt", _scrubbed_tail(CRASH_LOG_PATH, _CRASH_TAIL_LINES))

    return out_path
