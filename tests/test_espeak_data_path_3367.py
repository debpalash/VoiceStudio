"""espeak-ng must never exit(1) the backend over its data path (#3367).

Field report: KittenTTS on Windows killed the whole backend with
``Error processing file 'D:/a/espeakng-loader/.../phontab'``. espeak-ng copies
the data path it is given into a fixed-size buffer and opens it with the
narrow C runtime; a path it cannot open makes it fall back to the wheel's
build-time path and ``espeak_Initialize`` calls ``exit(1)``.

The real-library cases drive the bundled espeak-ng when ``espeakng_loader`` is
installed: a data path longer than espeak's buffer, or data espeak cannot
load, is rejected by espeak itself (fail-before), and
``ensure_espeak_data_path`` swaps in a short copy espeak accepts (pass-after).

App modules are resolved inside each test, never at module level, so a test
that purges and reloads them cannot leave this file patching a stale copy.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Files espeak_ng_Initialize loads; enough for a probe-able data directory.
_CORE_FILES = ("phontab", "phonindex", "phondata", "intonations")


def _ed():
    return importlib.import_module("utils.espeak_data")


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(_ed(), "_RESOLVED", None)
    for key in ("PHONEMIZER_ESPEAK_DATA_PATH", "ESPEAK_DATA_PATH", "OMNIVOICE_ESPEAK_DATA_CACHE"):
        monkeypatch.delenv(key, raising=False)


def _long_dir(tmp_path: Path) -> Path:
    deep = Path(os.path.realpath(tmp_path))
    while len(str(deep)) < 260:
        deep = deep / ("d" * 40)
    try:
        deep.mkdir(parents=True)
    except OSError:
        pytest.skip("long paths unavailable on this host")
    return deep


def _core_copy(source: Path, dest: Path) -> Path:
    dest.mkdir(parents=True)
    for name in _CORE_FILES:
        shutil.copy2(source / name, dest / name)
    return dest


@pytest.fixture
def real_loader():
    return pytest.importorskip("espeakng_loader")


def test_espeak_rejects_a_data_path_longer_than_its_buffer(real_loader, tmp_path):
    ed = _ed()
    lib = real_loader.get_library_path()
    data = Path(real_loader.get_data_path())
    assert ed.espeak_resolves(lib, str(data)) is True
    assert ed.espeak_resolves(lib, str(tmp_path / "missing")) is False
    long_copy = _core_copy(data, _long_dir(tmp_path) / "espeak-ng-data")
    assert ed.espeak_resolves(lib, str(long_copy)) is False


def test_espeak_rejects_data_it_cannot_load(real_loader, tmp_path):
    """A present-but-corrupt data folder must fail the probe, not exit later."""
    ed = _ed()
    lib = real_loader.get_library_path()
    data = Path(real_loader.get_data_path())
    good = _core_copy(data, tmp_path / "good")
    assert ed.espeak_resolves(lib, str(good)) is True

    corrupt = _core_copy(data, tmp_path / "corrupt")
    raw = bytearray((corrupt / "phondata").read_bytes())
    raw[:8] = b"\xff" * 8  # version + sample-rate header
    (corrupt / "phondata").write_bytes(bytes(raw))
    assert ed.espeak_resolves(lib, str(corrupt)) is False

    empty = _core_copy(data, tmp_path / "empty")
    (empty / "phondata").write_bytes(b"")
    assert ed.espeak_resolves(lib, str(empty)) is False


def test_long_wheel_path_is_replaced_by_a_short_copy(real_loader, tmp_path, monkeypatch):
    ed = _ed()
    long_data = _core_copy(Path(real_loader.get_data_path()), _long_dir(tmp_path) / "espeak-ng-data")
    cache = Path(os.path.realpath(tmp_path)) / "c"
    monkeypatch.setenv("OMNIVOICE_ESPEAK_DATA_CACHE", str(cache))
    loader = SimpleNamespace(
        get_library_path=real_loader.get_library_path,
        get_data_path=lambda: str(long_data),
    )

    chosen = ed.ensure_espeak_data_path(_loader=loader)

    assert chosen is not None and Path(chosen).is_relative_to(cache)
    assert (Path(chosen) / "phontab").is_file()
    assert ed.espeak_resolves(real_loader.get_library_path(), chosen) is True
    assert os.environ["PHONEMIZER_ESPEAK_DATA_PATH"] == chosen
    wrapper = pytest.importorskip("phonemizer.backend.espeak.wrapper")
    assert wrapper.EspeakWrapper._ESPEAK_DATA_PATH == chosen


def test_candidates_are_probed_in_the_form_phonemizer_passes(tmp_path, monkeypatch):
    """phonemizer resolves the data path (symlinks, 8.3 names) before espeak sees it."""
    ed = _ed()
    real = tmp_path / "real-data"
    real.mkdir()
    (real / "phontab").write_bytes(b"x")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    probed: list[str] = []
    monkeypatch.setattr(ed, "espeak_resolves", lambda lib, path: probed.append(path) or True)
    monkeypatch.setattr(ed, "_cache_roots", lambda: [])
    loader = SimpleNamespace(get_library_path=lambda: "libespeak", get_data_path=lambda: str(alias))

    chosen = ed.ensure_espeak_data_path(_loader=loader)

    assert probed == [str(real.resolve())]
    assert chosen == str(real.resolve())


def _fake_loader(tmp_path: Path, *, phontab: bool = True):
    data = tmp_path / "espeak-ng-data"
    data.mkdir()
    if phontab:
        (data / "phontab").write_bytes(b"x")
    return SimpleNamespace(get_library_path=lambda: "libespeak", get_data_path=lambda: str(data))


def test_unopenable_data_raises_instead_of_exiting(tmp_path, monkeypatch):
    ed = _ed()
    monkeypatch.setattr(ed, "espeak_resolves", lambda lib, path: False)
    monkeypatch.setattr(ed, "_cache_roots", lambda: [])
    with pytest.raises(ed.EspeakDataError, match="cannot open its data directory"):
        ed.ensure_espeak_data_path(_loader=_fake_loader(tmp_path))


def test_incomplete_wheel_data_raises(tmp_path):
    ed = _ed()
    with pytest.raises(ed.EspeakDataError, match="incomplete"):
        ed.ensure_espeak_data_path(_loader=_fake_loader(tmp_path, phontab=False))


def test_tampered_cache_copy_is_not_reused(tmp_path):
    ed = _ed()
    source = Path(_fake_loader(tmp_path).get_data_path())
    root = tmp_path / "cache"
    first = ed._cached_copy(source, root)
    assert first is not None and ed._cached_copy(source, root) == first
    (first / "phontab").write_bytes(b"tampered")
    assert ed._cached_copy(source, root) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_cache_from_a_group_writable_source_is_reused(tmp_path):
    ed = _ed()
    source = Path(_fake_loader(tmp_path).get_data_path())
    os.chmod(source, 0o775)
    root = tmp_path / "cache"
    first = ed._cached_copy(source, root)
    assert first is not None and ed._cached_copy(source, root) == first


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_shared_cache_directory_is_not_trusted(tmp_path):
    ed = _ed()
    source = Path(_fake_loader(tmp_path).get_data_path())
    root = tmp_path / "shared"
    (root / "voicestudio-espeak").mkdir(parents=True)
    os.chmod(root / "voicestudio-espeak", 0o777)
    assert ed._cached_copy(source, root) is None


def test_kittentts_load_checks_espeak_before_constructing(monkeypatch):
    """The guard runs before KittenTTS() — whose phonemizer init is the exit(1)."""
    ed = _ed()
    tts_backend = importlib.import_module("services.tts_backend")

    constructed = []
    monkeypatch.setitem(sys.modules, "kittentts", SimpleNamespace(KittenTTS=lambda *a: constructed.append(a)))

    def refuse():
        raise ed.EspeakDataError("no data")

    monkeypatch.setattr(ed, "ensure_espeak_data_path", refuse)
    with pytest.raises(ed.EspeakDataError):
        tts_backend.KittenTTSBackend()._ensure_loaded()
    assert constructed == []


@pytest.mark.parametrize(("owned", "reused"), [(False, False), (True, True), (None, True)])
def test_windows_cache_needs_this_users_ownership(tmp_path, monkeypatch, owned, reused):
    """A cache directory another Windows user owns is skipped; unknown falls back to the content check."""
    ed = _ed()
    source = Path(_fake_loader(tmp_path).get_data_path())
    root = tmp_path / "programdata"
    assert ed._cached_copy(source, root) is not None
    monkeypatch.setattr(ed.sys, "platform", "win32")
    monkeypatch.setattr(ed, "_windows_owned_by_me", lambda path: owned)
    assert (ed._cached_copy(source, root) is not None) is reused
