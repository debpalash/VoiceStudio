"""Containment must hold when the allowed root *is* a filesystem root.

``candidate.startswith(root + os.sep)`` looks equivalent to a containment test and
is not: on POSIX ``"/" + os.sep`` is ``"//"``, which no real child starts with, and
``"D:\\\\"`` does the same on Windows. Every legitimate path under such a root then
reads as an escape. ``OMNIVOICE_DATA_DIR`` is user-configurable and ``/`` (or a
drive root) is tolerated by the env-file writer, so the false rejection is
reachable -- it surfaced as a dub segment path "escaping DUB_DIR" and as a persona
bundle reporting that a readable reference clip was missing.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from core.path_security import is_within  # noqa: E402


def test_the_root_itself_is_within():
    assert is_within("/srv/data", "/srv/data")


def test_a_child_of_a_normal_root_is_within():
    assert is_within("/srv/data", "/srv/data/voices/clip.wav")
    assert is_within("/srv/data/", "/srv/data/voices/clip.wav")


def test_a_sibling_with_a_shared_prefix_is_not_within():
    # The case a bare startswith() gets wrong the other way round.
    assert not is_within("/srv/data", "/srv/database/clip.wav")
    assert not is_within("/srv/data", "/srv/data-2/clip.wav")


def test_traversal_is_not_within():
    assert not is_within("/srv/data", "/srv/data/../etc/passwd")
    assert not is_within("/srv/data", "/srv/data/voices/../../etc/passwd")


def test_an_unrelated_path_is_not_within():
    assert not is_within("/srv/data", "/etc/passwd")


@pytest.mark.skipif(os.name == "nt", reason="POSIX root spelling")
def test_a_posix_root_base_still_accepts_its_children():
    # The regression this helper exists for: the previous idiom rejected these.
    assert is_within("/", "/etc/passwd")
    assert is_within("/", "/srv/data/voices/clip.wav")
    legacy = lambda root, candidate: candidate != root and not candidate.startswith(root + os.sep)  # noqa: E731
    assert legacy("/", "/etc/passwd"), "the old idiom is the bug being fixed"


def test_a_windows_drive_root_still_accepts_its_children(tmp_path):
    if os.name != "nt":  # the helper uses the running host's path semantics
        pytest.skip("Windows path semantics")
    assert is_within("D:\\", "D:\\VoiceStudio\\voices\\clip.wav")
    assert not is_within("D:\\", "E:\\VoiceStudio\\clip.wav")


def test_the_dub_segment_helper_accepts_a_root_base(monkeypatch, tmp_path):
    """`config.dub_seg_path` refuses a real child when DUB_DIR resolves to a root."""
    from core import config

    monkeypatch.setattr(config, "DUB_DIR", "/")
    resolved = config.dub_seg_path("job-1", "seg-1")
    assert resolved.replace(os.sep, "/").endswith("job-1/seg_seg-1.wav")


def test_a_persona_reference_under_a_root_base_resolves(monkeypatch, tmp_path):
    """`persona_bundle._resolve_voice_file` must not call a readable clip missing."""
    from core import config
    from services import persona_bundle

    clip = tmp_path / "reference.wav"
    clip.write_bytes(b"RIFF")
    monkeypatch.setattr(config, "VOICES_DIR", str(tmp_path))
    monkeypatch.setattr(persona_bundle.os.path, "realpath", lambda value: str(value))
    assert persona_bundle._resolve_voice_file("reference.wav") == str(clip)


def test_an_escaping_persona_reference_is_still_refused(monkeypatch, tmp_path):
    from core import config
    from services import persona_bundle

    monkeypatch.setattr(config, "VOICES_DIR", str(tmp_path))
    assert persona_bundle._resolve_voice_file("../../etc/passwd") is None
    assert persona_bundle._resolve_voice_file("sub/dir.wav") is None
