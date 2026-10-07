"""Windows registry GUIDs in shipped code must be real ones (#2620).

A typo'd device-setup class GUID fails silently: ``winreg.OpenKey`` raises on
every host, a never-raises wrapper turns that into "nothing found", and a fake
registry that ignores the path keeps CI green. That is how the Windows GPU
inventory returned ``()`` on every machine. This scans every tracked source
file so the next such constant fails here instead of in production.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# System-defined device setup classes (devguid.h). Every one shares the
# ``-e325-11ce-bfc1-08002be10318`` tail; extend this table when code starts
# reading a new class key.
_KNOWN_SETUP_CLASSES = {
    "4d36e968-e325-11ce-bfc1-08002be10318": "Display",
    "4d36e96c-e325-11ce-bfc1-08002be10318": "Media (sound)",
    "4d36e972-e325-11ce-bfc1-08002be10318": "Net",
    "4d36e97d-e325-11ce-bfc1-08002be10318": "System",
}

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# A GUID used as a ...\Control\Class\{GUID} registry path (any slash escaping).
_CLASS_PATH = re.compile(r"Control[\\/]+Class[\\/\"'\s)rR(]*\{(" + _GUID + r")\}")
# Anything shaped like a system setup class GUID, wherever it appears.
_SETUP_CLASS_LIKE = re.compile(r"\b(4d36e9[0-9a-fA-F]{2}-e325-[0-9a-fA-F-]+)", re.I)

_SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".rs", ".ps1", ".nsh", ".nsi", ".bat", ".cmd"}


def _tracked_sources() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [
        _REPO / p for p in out
        if Path(p).suffix in _SOURCE_EXT and not p.startswith("tests/")
        and "node_modules" not in p
    ]


def _findings(text: str) -> list[str]:
    bad = []
    for m in _CLASS_PATH.finditer(text):
        if m.group(1).lower() not in _KNOWN_SETUP_CLASSES:
            bad.append(m.group(1))
    for m in _SETUP_CLASS_LIKE.finditer(text):
        guid = m.group(1).lower()[:36]
        if guid not in _KNOWN_SETUP_CLASSES:
            bad.append(guid)
    return bad


def test_detector_catches_the_2620_typo():
    shipped_typo = 'r"SYSTEM\\CurrentControlSet\\Control\\Class"\n    r"\\{4d36e968-e325-11cd-8000-0000f3ed53be}"'
    assert _findings(shipped_typo)
    real = 'r"SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}"'
    assert _findings(real) == []


def test_every_setup_class_guid_in_source_is_real():
    offenders = []
    for path in _tracked_sources():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for guid in _findings(text):
            offenders.append(f"{path.relative_to(_REPO)}: {guid}")
    assert not offenders, (
        "Unknown Windows device-setup class GUID(s) — check devguid.h, then "
        f"extend _KNOWN_SETUP_CLASSES if the class is real: {offenders}"
    )
