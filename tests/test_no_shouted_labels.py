"""UI labels are sentence case, never baked-in capitals.

An all-caps English string ("TARGET LANGUAGES", "FIT SEL") shouts in the app and
gets copied into every translation that way. Visual emphasis belongs in CSS; the
catalog holds the words. Acronyms, typed confirmation words and the Pro page's
kicker style are the deliberate exceptions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CATALOG = (
    Path(__file__).resolve().parents[1] / "electron/src/renderer/src/i18n/locales/en.json"
)

_SHOUTED = re.compile(r"[A-Z][A-Z &/'.-]{3,}")
_ALLOWED_KEYS = {
    "about.vram",  # acronym
    "settings.uninstall_confirm_word",  # the word the user must type
    "settings.reset_confirm_word",
}
_ALLOWED_PREFIXES = ("proPage.",)  # styled kicker labels on the Pro page


def _strings(node: dict, prefix: str = ""):
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _strings(value, f"{prefix}{key}.")
        elif isinstance(value, str):
            yield f"{prefix}{key}", value


def test_english_labels_are_not_all_caps() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    shouted = [
        f"{key}: {value!r}"
        for key, value in _strings(catalog)
        if _SHOUTED.fullmatch(value)
        and key not in _ALLOWED_KEYS
        and not key.startswith(_ALLOWED_PREFIXES)
    ]
    assert not shouted, "Write these in sentence case:\n" + "\n".join(shouted)
