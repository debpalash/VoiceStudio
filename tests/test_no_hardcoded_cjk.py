"""Hard rule: no hardcoded non-English (CJK) *user-facing* text outside the
translation layer.

UI strings must go through i18n (``electron/src/shared/i18n/locales/*.json`` via
``t('...')``); native language names live in ``electron/src/shared/i18n/index.ts``.
This guards against contributors hardcoding Chinese/Japanese/Korean display
strings in component or app code (see CLAUDE.md > Conventions).

Functional CJK is permitted and tracked in ``_ALLOWED_FILES`` below:
text-processing regexes, model/engine vocabulary & identifiers, localized
error matching, demo/eval data, and test fixtures. To add a new legitimate
functional-CJK file, extend ``_ALLOWED_FILES`` with a one-line justification.
"""
import os
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# CJK punctuation, kana, CJK Ext-A, CJK unified ideographs, hangul, and
# fullwidth forms. (This enforcement file lives under tests/, which is
# allowlisted, so its own range literals don't trip the check.)
_CJK = re.compile(
    "[　-〿぀-ヿ㐀-䶿一-鿿가-힯＀-￯]"
)

_SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "__pycache__",
    ".venv", "venv", "target", ".specify", ".claude", ".pytest_cache",
}
_SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".pdf", ".mp3", ".wav",
    ".mp4", ".lock", ".bin", ".onnx", ".safetensors",
}

# The translation layer: locale JSON + native language names (LANGUAGES).
# Plus design-spec docs under docs/specs/, which legitimately quote functional
# CJK (test-fixture descriptions, model/engine identifiers like CosyVoice
# speaker IDs, multilingual sample text) — they are documentation, not shipped
# UI strings, so they belong on the same footing as the allowlisted docs below.
_ALLOWED_PREFIXES = ("electron/src/shared/i18n/", "electron/src/renderer/src/i18n/", "docs/specs/")

# Functional / data / documentation files where CJK is intentional and required.
_ALLOWED_FILES = {
    "electron/src/main/blank-window-guard.ts",
    # Documentation & translated docs
    "README.md",                                  # native language-switcher link
    "README_CN.md",                               # Chinese README (a translation)
    "docs/data_preparation.md",                   # multilingual example payloads
    "docs/voice-design.md",                       # EN/CJK attribute mapping table
    "docs/engines/omnivoice.md",                  # pinyin pronunciation-control example (functional CJK)
    "docs/superpowers/specs/2026-05-31-voice-gallery-design.md",  # Chinese-dialect taxonomy reference table
    "examples/README.md",                         # multilingual example payloads
    # Text-processing (CJK punctuation inside sentence/clause-splitting regexes)
    "backend/services/segmentation.py",
    "backend/services/sentence_chunker.py",       # streaming-TTS terminator tables (Patter port, Wave 1.4)
    "backend/services/subtitle_segmenter.py",
    "backend/services/text_normalization.py",  # spoken range word per language ("20~30" must not read as one number)
    "backend/core/http_headers.py",               # docstring quotes the CJK filename that 500'd the header (#1262)
    "electron/src/shared/components/DubSegmentRow.jsx",
    "electron/src/shared/components/StoriesEditor.jsx",
    "electron/src/shared/utils/voiceInstruct.js",
    "omnivoice/utils/text.py",
    # Model / engine vocabulary & identifiers (the model/engine requires these)
    "backend/services/tts_backend.py",            # CosyVoice speaker IDs
    "backend/core/personalities.py",              # Chinese-dialect showcase preset
    "backend/core/archetypes.py",                 # Chinese-dialect + JA/KO multilingual preview sample text
    "backend/core/describe_voice.py",             # pinyin → Chinese-dialect token mapping (model vocabulary, #317)
    "electron/src/shared/utils/constants.js",            # Chinese-dialect picker names
    "omnivoice/models/omnivoice.py",              # instruct-mode vocabulary
    "omnivoice/utils/duration.py",
    "omnivoice/utils/voice_design.py",            # EN/CJK attribute maps
    "backend/migrations/versions/0007_rebuild_poisoned_design_instruct.py",  # frozen CJK dialect-tag snapshot for the instruct heal (#564)
    # Localized error matching (classify OS errors reported in Chinese, #72)
    "electron/src/shared/utils/errorDocsMap.ts",
    # WER evaluation data
    "omnivoice/eval/wer/fleurs.py",
    "omnivoice/eval/wer/punctuations.lst",
    # CLI demo (bilingual demo labels; not the shipped app)
    "omnivoice/cli/demo.py",
    # Demo-audio generation scripts (multilingual TTS sample text)
    "scripts/build_demos.sh",
    "scripts/build_dub_demo.sh",
    "scripts/dub_demo_scripts.json",              # the five dub paragraphs + their native language labels
    # Rendered dubbing-demo bundle: the demo IS a dub into Chinese and
    # Japanese, so its subtitles and manifest carry that text as data. Not UI
    # strings — nothing here is translated, it is the content being shown.
    "backend/assets/samples/demo/dubbing/dubbed_zh.srt",
    "backend/assets/samples/demo/dubbing/dubbed_ja.srt",
    "backend/assets/samples/demo/dubbing/manifest.json",
}


def _is_allowed(rel: str) -> bool:
    if rel in _ALLOWED_FILES:
        return True
    if any(rel.startswith(p) for p in _ALLOWED_PREFIXES):
        return True
    base = os.path.basename(rel)
    # Test fixtures legitimately carry multilingual sample text.
    if ".test." in base or base.startswith("test_") or base.endswith("_test.py"):
        return True
    parts = rel.split("/")
    if "tests" in parts or "test" in parts:
        return True
    return False


def _iter_source_files():
    # Scan only git-TRACKED files: the rule governs the committed codebase,
    # not local untracked/vendored experiments (which also never reach CI).
    import subprocess
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(_REPO), capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        names = [n for n in out.split("\0") if n]
        if names:
            for n in names:
                if os.path.splitext(n)[1].lower() in _SKIP_EXT:
                    continue
                yield _REPO / n
            return
    except Exception:
        pass
    # Fallback (not a git checkout): filesystem walk.
    for dirpath, dirnames, filenames in os.walk(_REPO):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in _SKIP_EXT:
                continue
            yield Path(dirpath) / name


def test_no_hardcoded_cjk_outside_locales():
    offenders = {}
    for path in _iter_source_files():
        rel = path.relative_to(_REPO).as_posix()
        if _is_allowed(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = [
            (i + 1, line.strip()[:80])
            for i, line in enumerate(text.splitlines())
            if _CJK.search(line)
        ]
        if hits:
            offenders[rel] = hits

    if offenders:
        msg = [
            "Hardcoded non-English (CJK) text found outside the translation layer.",
            "Move user-facing strings into electron/src/shared/i18n/locales/*.json (or use English).",
            "If functional CJK (regex/model-vocab/data/fixture), add the file to _ALLOWED_FILES here.",
            "",
        ]
        for rel, hits in sorted(offenders.items()):
            msg.append(f"  {rel}:")
            for ln, snippet in hits[:3]:
                msg.append(f"    L{ln}: {snippet}")
        pytest.fail("\n".join(msg))
