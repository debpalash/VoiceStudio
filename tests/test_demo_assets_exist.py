"""Every demo asset the UI advertises actually ships.

This exists because they did not. `personalities.py` has carried a
``preview_url`` for each of the seven voice-design presets since they were
added, and the WAVs behind them were never committed — the demo tooling that
renders them (``scripts/build_demos.sh``) hard-requires macOS ``say``, so on any
other machine the files simply never appeared. Result: seven preview buttons in
the voice picker that returned 404, plus three dictation replay clips and the
whole dubbing demo in the same state.

Nothing caught it, because a missing static file is not an import error and not
a failing request in any test — the app just plays nothing. So the check is
mechanical and lives here: every path the code hands to the browser is resolved
against the directory ``main.py`` actually mounts.

If this fails after adding a preset, render the assets rather than deleting the
check:

    python3 scripts/render_demos_omnivoice.py     # voice design + dictation
    python3 scripts/render_dub_demo_audio.py      # dubbing audio
    bash scripts/build_dub_demo.sh                # dubbing videos + manifest
"""
from __future__ import annotations

import json
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_REPO_ROOT, "backend")

# The mount point. `main.py`: app.mount("/demo_audio", StaticFiles(directory=…))
# over backend/assets/samples — so a "/demo_audio/x/y.wav" URL is that file
# under this directory, and nothing else.
_DEMO_ROOT = os.path.join(_BACKEND, "assets", "samples")

_DICTATION_DEMO = os.path.join(
    _REPO_ROOT, "electron", "src", "shared", "components", "DictationDemo.jsx"
)


def _resolve(url: str) -> str:
    """A /demo_audio/... URL → the file on disk it is served from."""
    assert url.startswith("/demo_audio/"), url
    return os.path.join(_DEMO_ROOT, url[len("/demo_audio/") :])


def _personalities():
    import sys

    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    from core.personalities import PERSONALITIES  # noqa: PLC0415

    return PERSONALITIES


def test_the_demo_mount_points_where_this_test_thinks_it_does():
    """Pin the mount, so moving it fails here rather than in the browser."""
    main_py = open(os.path.join(_BACKEND, "main.py"), encoding="utf-8").read()
    assert 'os.path.join(os.path.dirname(__file__), "assets", "samples")' in main_py
    assert 'app.mount("/demo_audio"' in main_py


def test_every_voice_design_preview_exists():
    """Every preset that advertises a preview has the audio to back it.

    Presets are read inside the test, not in a `parametrize` argument:
    parametrize is evaluated at COLLECTION time, which would import app code
    before any test runs and leave `core.personalities` in `sys.modules` for
    every later test to inherit.
    """
    presets = [p for p in _personalities() if p.get("preview_url")]
    assert presets, "no voice-design preset advertises a preview"
    for preset in presets:
        path = _resolve(preset["preview_url"])
        assert os.path.isfile(path), (
            f"{preset['id']} advertises {preset['preview_url']} but {path} is missing. "
            "Render it with scripts/render_demos_omnivoice.py --only design."
        )
        assert os.path.getsize(path) > 8000, f"{path} is too small to be real audio"


def _dictation_wavs() -> list[str]:
    """The replay clips DictationDemo.jsx posts to /transcribe."""
    source = open(_DICTATION_DEMO, encoding="utf-8").read()
    return re.findall(r"wav:\s*'(/demo_audio/[^']+)'", source)


def test_dictation_demo_lists_its_scripts():
    assert len(_dictation_wavs()) == 3, "DictationDemo should offer three scripts"


@pytest.mark.parametrize("url", _dictation_wavs())
def test_every_dictation_clip_exists(url):
    path = _resolve(url)
    assert os.path.isfile(path), (
        f"DictationDemo replays {url} but {path} is missing. "
        "Render it with scripts/render_demos_omnivoice.py --only dictation."
    )
    assert os.path.getsize(path) > 8000, f"{path} is too small to be real audio"


def test_the_cloning_demo_pair_exists():
    for name in ("demo_voice.wav", "demo_clone_output.wav"):
        assert os.path.isfile(os.path.join(_DEMO_ROOT, name)), name


def test_the_dubbing_demo_manifest_and_every_file_it_names_exist():
    """The Dub workspace reads this manifest and plays what it lists."""
    manifest_path = _resolve("/demo_audio/demo/dubbing/manifest.json")
    assert os.path.isfile(manifest_path), (
        "The dubbing demo manifest is missing. Build it with "
        "scripts/render_dub_demo_audio.py then scripts/build_dub_demo.sh."
    )
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    entries = [manifest["source"], *manifest["dubbed"]]
    assert len(entries) == 5, "one source plus four dubs"
    directory = os.path.dirname(manifest_path)
    for entry in entries:
        for key in ("video", "srt"):
            path = os.path.join(directory, entry[key])
            assert os.path.isfile(path), f"{entry['code']}: {entry[key]} missing"
        # A manifest that names a video whose subtitle says something else is
        # the one failure a viewer cannot tell from a bad dub.
        srt = open(os.path.join(directory, entry["srt"]), encoding="utf-8").read()
        assert entry["script"] in srt, f"{entry['code']}: subtitle does not match the script"
