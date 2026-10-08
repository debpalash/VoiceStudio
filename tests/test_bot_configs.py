"""Review-bot configs must describe the repository that exists today.

CodeRabbit (``.coderabbit.yaml``) and Greptile (``greptile.json``) review every
PR with instructions that name repo paths. When the Tauri shell and the
``frontend/`` UI were removed, those instructions kept pointing reviewers at
``frontend/src/i18n/locales`` and ``frontend/src-tauri`` mirrors, so the bots
enforced rules against files that no longer exist. This test fails when either
config references a missing repo path, a path-scoped review rule matches no
tracked file, or the removed ``frontend/`` / ``src-tauri`` trees reappear.

Extraction is deliberately conservative: only tokens that start with a
top-level tracked directory (``backend/...``) or bare file names with a config
extension (``CLAUDE.md``) are checked; glob tokens are checked up to their
first wildcard.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = (".coderabbit.yaml", "greptile.json")
_GLOB_CHARS = "*?{["
_BANNED = ("src-tauri", "frontend/")


def _tracked() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git ls-files unavailable")
    return [line for line in out.splitlines() if line]


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "{":
            end = pattern.index("}", i)
            options = pattern[i + 1 : end].split(",")
            out, i = out + "(?:" + "|".join(map(re.escape, options)) + ")", end + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def _prefix_exists(token: str) -> bool:
    """True when the non-glob part of ``token`` names an existing path."""
    cut = min((token.find(c) for c in _GLOB_CHARS if c in token), default=-1)
    if cut == -1:
        return (ROOT / token).exists()
    prefix = token[:cut]
    if "/" not in prefix:
        return True  # glob starts at the repo root, e.g. "**/*.lock"
    return (ROOT / prefix[: prefix.rindex("/")]).is_dir()


def _path_tokens(text: str, top_dirs: set[str], basenames: set[str]) -> list[str]:
    dirs = "|".join(sorted(map(re.escape, top_dirs), key=len, reverse=True))
    found = re.findall(rf"(?<![\w./*-])((?:{dirs})/[\w./*{{}}?,-]*)", text)
    tokens = []
    for raw in found:
        # Commas belong to brace globs only; prose commas end the token.
        token = re.sub(r",(?![^{]*\})", " ", raw).split(" ")[0].rstrip(".,")
        tokens.append(token)
    for name in re.findall(r"(?<![\w./*-])([\w-]+\.(?:md|toml|jsonc?|ya?ml|lock))\b", text):
        if name not in basenames:
            tokens.append(name)
    return tokens


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _config_text() -> dict[str, str]:
    """Raw YAML (comments count) and decoded JSON strings (no ``\\n`` escapes)."""
    texts = {}
    for name in CONFIGS:
        raw = (ROOT / name).read_text(encoding="utf-8")
        texts[name] = "\n".join(_strings(json.loads(raw))) if name.endswith(".json") else raw
    return texts


def test_bot_configs_do_not_mention_removed_ui_trees():
    hits = [f"{name}: {banned}" for name, text in _config_text().items() for banned in _BANNED if banned in text]
    assert not hits, (
        f"Bot configs mention removed paths {hits}; Electron (electron/) is the only UI "
        "and the Tauri shell is gone — point the rule at the live path."
    )


def test_bot_config_path_references_exist():
    tracked = _tracked()
    top_dirs = {path.split("/", 1)[0] for path in tracked if "/" in path}
    basenames = {path.rsplit("/", 1)[-1] for path in tracked}
    missing = []
    for name, text in _config_text().items():
        for token in _path_tokens(text, top_dirs, basenames):
            if "/" in token and not _prefix_exists(token):
                missing.append(f"{name}: {token}")
            elif "/" not in token:
                missing.append(f"{name}: {token} (no tracked file has this name)")
    assert not missing, "Review-bot configs reference missing paths:\n" + "\n".join(missing)


def test_bot_config_globs_match_tracked_files():
    tracked = _tracked()
    coderabbit = yaml.safe_load((ROOT / ".coderabbit.yaml").read_text(encoding="utf-8"))
    greptile = json.loads((ROOT / "greptile.json").read_text(encoding="utf-8"))
    reviews = coderabbit["reviews"]
    scoped = [entry["path"] for entry in reviews["path_instructions"]]
    scoped += coderabbit["knowledge_base"]["code_guidelines"]["filePatterns"]
    scoped += greptile["customContext"]["files"]
    dead = [glob for glob in scoped if not any(_glob_regex(glob).match(p) for p in tracked)]
    assert not dead, f"Review rules scoped to globs that match no tracked file: {dead}"
    # Ignore lists may legitimately match nothing yet; only their roots must exist.
    ignores = [p.lstrip("!") for p in reviews["path_filters"]]
    ignores += greptile["ignorePatterns"].split("\n")
    gone = [p for p in ignores if not _prefix_exists(p)]
    assert not gone, f"Review-bot ignore patterns under missing directories: {gone}"


def test_extractor_flags_stale_paths():
    """Guard the extractor itself so it cannot silently stop matching."""
    tokens = _path_tokens(
        "Check frontend/src/i18n/locales/*.json, backend/core/version.py and NOPE.md.",
        {"frontend", "backend"},
        {"version.py"},
    )
    assert tokens == ["frontend/src/i18n/locales/*.json", "backend/core/version.py", "NOPE.md"]
    assert not _prefix_exists("frontend/src-tauri/Cargo.toml")
    assert _prefix_exists("electron/src/{shared,renderer}/**/*.tsx")
    assert _glob_regex("electron/src/{shared,renderer}/**/*.{js,tsx}").match(
        "electron/src/shared/components/Foo.tsx"
    )
