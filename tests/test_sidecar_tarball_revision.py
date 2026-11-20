"""The git-less source fallback must tie the archive to the pinned commit.

`_download_and_extract` used to accept any archive that unpacked to a single
top-level directory and then handed that tree to `uv pip install`, which runs its
build backend. The pinned SHA appeared only in the URL text and in a marker the
installer wrote itself, and `_source_present` then trusted that marker -- so a
substituted archive, or a redirect that landed somewhere else, was installed.

These tests drive the function directly with a generated tarball, so no network,
git or uv is involved.
"""
from __future__ import annotations

import io
import pathlib
import sys
import tarfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

import services.sidecar_install as si  # noqa: E402

PIN = "a" * 40
OTHER = "b" * 40


def _tarball(root_name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        payload = b"[project]\nname='fake'\n"
        info = tarfile.TarInfo(f"{root_name}/pyproject.toml")
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class _FakeStream:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self._payload


def _fake_httpx(monkeypatch, root_name: str) -> None:
    import httpx

    monkeypatch.setattr(httpx, "stream", lambda method, url, **kw: _FakeStream(_tarball(root_name)))


@pytest.fixture()
def job() -> dict:
    return {"engine_id": "fake-side", "log": []}


def test_a_url_pinning_another_commit_is_refused_before_downloading(tmp_path, job):
    urls: list[str] = []

    class _NeverStream:
        def __init__(self, *_args, **_kwargs):
            urls.append("called")

    import httpx

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx, "stream", _NeverStream)
        with pytest.raises(si._StepError) as excinfo:
            si._download_and_extract(
                job,
                f"https://github.com/o/r/archive/{OTHER}.tar.gz",
                tmp_path / "dest",
                tmp_path,
                "OMNIVOICE_FAKE_DIR",
                expected_revision=PIN,
            )
    assert "pins" in str(excinfo.value)
    assert urls == [], "the mismatch must be decided before any request"


def test_an_archive_naming_another_commit_is_refused(tmp_path, job, monkeypatch):
    _fake_httpx(monkeypatch, f"r-{OTHER}")
    with pytest.raises(si._StepError) as excinfo:
        si._download_and_extract(
            job,
            f"https://github.com/o/r/archive/{PIN}.tar.gz",
            tmp_path / "dest",
            tmp_path,
            "OMNIVOICE_FAKE_DIR",
            expected_revision=PIN,
        )
    assert "does not match the pinned commit" in str(excinfo.value)
    assert not (tmp_path / "dest").exists(), "a rejected archive must not be installed"


def test_an_archive_naming_the_pinned_commit_is_installed(tmp_path, job, monkeypatch):
    _fake_httpx(monkeypatch, f"r-{PIN}")
    dest = tmp_path / "dest"
    si._download_and_extract(
        job,
        f"https://github.com/o/r/archive/{PIN}.tar.gz",
        dest,
        tmp_path,
        "OMNIVOICE_FAKE_DIR",
        expected_revision=PIN,
    )
    assert (dest / "pyproject.toml").is_file()


def test_a_spec_without_a_revision_keeps_working(tmp_path, job, monkeypatch):
    """A synthetic or unpinned URL (as several existing tests use) is unaffected."""
    _fake_httpx(monkeypatch, "fake-side-main")
    dest = tmp_path / "dest"
    si._download_and_extract(
        job, "https://example.test/fake-side-main.tar.gz", dest, tmp_path, "OMNIVOICE_FAKE_DIR"
    )
    assert (dest / "pyproject.toml").is_file()


def test_every_shipped_spec_ties_its_url_to_its_revision():
    """The invariant the runtime check enforces must hold for what we ship."""
    import re

    offenders = []
    for engine_id, spec in si.SPECS.items():
        if not spec.tarball_url:
            continue
        match = re.search(r"/([0-9a-f]{40})(?:\.tar\.gz)?$", spec.tarball_url)
        if match is None:
            offenders.append(f"{engine_id}: URL pins no commit")
        elif spec.source_revision and match.group(1) != spec.source_revision:
            offenders.append(f"{engine_id}: URL {match.group(1)[:8]} != spec {spec.source_revision[:8]}")
        if not spec.source_revision:
            offenders.append(f"{engine_id}: source_revision absent")
    assert not offenders, "\n".join(offenders)
