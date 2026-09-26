"""Keep localized dictation recommendation copy aligned with model policy."""

from __future__ import annotations

import json
from pathlib import Path


_LOCALES = Path(__file__).resolve().parents[1] / "electron" / "src" / "shared" / "i18n" / "locales"


def test_recommended_badge_copy_belongs_to_whisper_tiny_in_every_locale():
    natural_prefixes = {
        "ja": "おすすめ",
        "uk": "рекомендовано",
        "vi": "khuyến nghị",
    }
    for path in sorted(_LOCALES.glob("*.json")):
        panel = json.loads(path.read_text(encoding="utf-8"))["voicePanel"]
        badge = panel["badge_recommended"].casefold()
        recommendation = natural_prefixes.get(path.stem, badge)
        descriptions = panel["model_desc"]
        assert recommendation not in descriptions["sherpa-parakeet-tdt-v3"].casefold(), path.name
        assert recommendation in descriptions["sherpa-whisper-tiny"].casefold(), path.name
