"""Keep CJK user-facing text in translation catalogs.

UI copy must use i18n ``t('...')`` keys backed by locale JSON. Native language
names belong in ``electron/src/shared/i18n/index.ts``. Functional CJK may be
allowlisted below, with a justification for every file.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]")
_SKIP_EXTENSIONS = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf",
    # documents
    ".pdf",
    # audio / video
    ".mp3", ".wav", ".mp4",
    # lockfiles
    ".lock",
    # model weights
    ".bin", ".onnx", ".safetensors",
}
_SKIP_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    "target", ".specify", ".claude", ".pytest_cache",
}
_ALLOWED_PREFIXES = (
    "electron/src/shared/i18n/",
    "electron/src/renderer/src/i18n/",
    "docs/specs/",
    "docs/readme/",  # translated READMEs and their native language-switcher links
)
_ALLOWED_FILES = {
    "mobile/contract/tts/zipvoice-lexicon.txt",  # Pinned multilingual pronunciation data, not UI copy.
    "electron/src/main/blank-window-guard.ts",  # Matches localized window text.
    "README.md",  # Presents translated project copy.
    "docs/data_preparation.md",  # Includes multilingual data examples.
    "docs/voice-design.md",  # Documents multilingual voice prompts.
    "docs/engines/omnivoice.md",  # Documents engine vocabulary.
    "docs/superpowers/specs/2026-05-31-voice-gallery-design.md",  # Preserves design examples.
    "examples/README.md",  # Contains multilingual examples.
    "backend/services/segmentation.py",  # CJK-aware segmentation rules.
    "backend/services/sentence_chunker.py",  # CJK sentence-boundary logic.
    "backend/services/subtitle_segmenter.py",  # CJK subtitle segmentation.
    "backend/services/text_normalization.py",  # CJK normalization patterns.
    "backend/core/http_headers.py",  # Parses localized HTTP filenames.
    "electron/src/shared/components/DubSegmentRow.jsx",  # Functional language-name data.
    "electron/src/shared/components/StoriesEditor.jsx",  # Functional multilingual samples.
    "electron/src/shared/utils/voiceInstruct.js",  # CJK instruction vocabulary.
    "omnivoice/utils/text.py",  # Core multilingual text processing.
    "backend/services/tts_backend.py",  # Engine speaker vocabulary.
    "backend/core/personalities.py",  # Model prompt vocabulary.
    "backend/core/archetypes.py",  # Voice archetype vocabulary.
    "backend/core/describe_voice.py",  # Voice-description vocabulary.
    "electron/src/shared/utils/constants.js",  # Engine language constants.
    "omnivoice/models/omnivoice.py",  # Model language tokens.
    "omnivoice/utils/duration.py",  # CJK duration rules.
    "omnivoice/utils/voice_design.py",  # Voice-design model vocabulary.
    "backend/migrations/versions/0007_rebuild_poisoned_design_instruct.py",  # Migrates model prompts.
    "electron/src/shared/utils/errorDocsMap.ts",  # Recognizes localized errors.
    "omnivoice/eval/wer/fleurs.py",  # Multilingual evaluation data.
    "omnivoice/eval/wer/punctuations.lst",  # Evaluation punctuation corpus.
    "omnivoice/cli/demo.py",  # Multilingual demo content.
    "scripts/build_demos.sh",  # Builds multilingual demos.
    "scripts/build_dub_demo.sh",  # Builds localized dub samples.
    "scripts/dub_demo_scripts.json",  # Stores multilingual demo scripts.
    "backend/assets/samples/demo/dubbing/dubbed_zh.srt",  # Chinese demo subtitles.
    "backend/assets/samples/demo/dubbing/dubbed_ja.srt",  # Japanese demo subtitles.
    "backend/assets/samples/demo/dubbing/manifest.json",  # Multilingual demo metadata.
}


def _is_allowed(rel: str) -> bool:
    path = Path(rel)
    basename = path.name
    return (
        rel in _ALLOWED_FILES
        or rel.startswith(_ALLOWED_PREFIXES)
        or ".test." in basename
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or any(part in {"tests", "test"} for part in path.parts)
    )


def _tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=_ROOT, capture_output=True,
            check=True, timeout=30,
        )
        files = [item.decode() for item in result.stdout.split(b"\0") if item]
        if files:
            return files
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        pass

    files = []
    for root, dirs, names in os.walk(_ROOT):
        dirs[:] = [name for name in dirs if name not in _SKIP_DIRECTORIES]
        base = Path(root)
        files.extend(str((base / name).relative_to(_ROOT)) for name in names)
    return files


def test_no_hardcoded_cjk_outside_locales():
    offenders: dict[str, list[tuple[int, str]]] = {}
    for rel in _tracked_files():
        path = _ROOT / rel
        if _is_allowed(rel) or path.suffix.lower() in _SKIP_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = [
            (number, line.strip()[:80])
            for number, line in enumerate(text.splitlines(), 1)
            if _CJK.search(line)
        ]
        if hits:
            offenders[rel] = hits

    details = [
        "Hardcoded CJK was found outside the translation layer.",
        "Move user-facing strings into the locale JSON catalogs.",
        "For functional CJK, extend _ALLOWED_FILES with a justification.",
    ]
    for rel in sorted(offenders):
        details.append(rel)
        details.extend(f"  L{line}: {snippet}" for line, snippet in offenders[rel][:3])
    assert not offenders, "\n".join(details)


@pytest.mark.parametrize(
    "path",
    [
        "electron/src/shared/i18n/locales/zh-CN.json",
        "widget.test.ts",
        "test_widget.py",
        "widget_test.py",
        "pkg/tests/widget.py",
        "pkg/test/widget.py",
    ],
)
def test_is_allowed_patterns(path):
    assert _is_allowed(path)
