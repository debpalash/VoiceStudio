"""The tray menu uses its own strings, never ones borrowed from other screens.

The Quit item used to reuse the crash report's "Exit" field, so it read as
"exit code" (zh-CN, ko), "exit door" (ja, zh-TW) or "motorway exit" (de).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "electron/src/renderer/src/i18n/locales"
TRAY_KEYS = ("open", "start_dictation", "stop_dictation", "settings", "quit")


def test_the_tray_menu_reads_only_tray_keys():
    app = (ROOT / "electron/src/renderer/src/app.tsx").read_text(encoding="utf-8")
    start = app.index("?.labels({")
    block = app[start:app.index(".catch(", start)]
    keys = re.findall(r"t\('([^']+)'", block)
    assert keys and all(k.startswith("tray.") or k == "app.name" for k in keys), keys
    assert {k.split(".", 1)[1] for k in keys if k.startswith("tray.")} == set(TRAY_KEYS)


def test_every_locale_has_distinct_tray_strings():
    for path in sorted(LOCALES.glob("*.json")):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        tray = catalog["tray"]
        assert all(tray[k].strip() for k in TRAY_KEYS), path.name
        assert "{{app}}" in tray["open"], path.name
        assert tray["quit"] != catalog["crash"]["field_exit"], path.name
