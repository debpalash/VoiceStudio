"""Vendored wheels (#4253) are verifiable offline.

Contents versus the upstream sdist are checked by
`scripts/build_vendored_wheels.py --verify` (needs network). Here, with no
network: the wheel is the one uv.lock pins, its RECORD is consistent, it holds
only pure-Python/data files and the scripts the sdist declares, and its
METADATA names the locked package and version.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import tomllib
import zipfile
from email import message_from_string
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WHEELS = ROOT / "vendor" / "wheels"
ENTRIES = json.loads((WHEELS / "SOURCES.json").read_text(encoding="utf-8"))["wheels"]
LOCK = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

_ALLOWED_SUFFIXES = {".py", ".yaml", ".typed", ".txt", ""}
_FORBIDDEN_SUFFIXES = {".so", ".pyd", ".dll", ".dylib", ".exe", ".pth", ".pyc", ".bat", ".cmd"}
# `.data/scripts/*` is only what the sdist's setup.py declares (antlr4's `pygrun`).
_DECLARED_SCRIPTS = {"antlr4-python3-runtime": {"antlr4_python3_runtime-4.9.3.data/scripts/pygrun"}}
# antlr4-python3-runtime 4.9.3's sdist ships no licence file (BSD-3-Clause in its METADATA).
_NO_UPSTREAM_LICENCE = {"antlr4-python3-runtime"}


def _locked(name: str) -> dict:
    (pkg,) = [p for p in LOCK["package"] if p["name"] == name and "path" in p["source"]]
    return pkg


def test_every_path_wheel_in_the_lock_is_recorded() -> None:
    locked = {p["name"] for p in LOCK["package"] if "path" in p["source"]}
    assert locked == {e["name"] for e in ENTRIES}
    assert {p.name for p in WHEELS.glob("*.whl")} == {e["wheel"] for e in ENTRIES}
    for e in ENTRIES:
        assert e["sdist_url"].startswith("https://files.pythonhosted.org/")
        assert len(e["sdist_sha256"]) == 64


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["name"])
def test_wheel_is_the_locked_pure_python_wheel(entry: dict) -> None:
    path = WHEELS / entry["wheel"]
    pkg = _locked(entry["name"])
    assert pkg["version"] == entry["version"]
    assert pkg["source"]["path"] == f"vendor/wheels/{entry['wheel']}"
    assert [w["hash"] for w in pkg["wheels"]] == ["sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()]

    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        info = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta = message_from_string(z.read(info).decode("utf-8"))
        assert meta["Name"].lower().replace("_", "-") == entry["name"]
        assert meta["Version"] == entry["version"]
        assert "py3-none-any" in path.name or "py2.py3-none-any" in path.name
        assert "Root-Is-Purelib: true" in z.read(next(n for n in names if n.endswith(".dist-info/WHEEL"))).decode()

        record_name = next(n for n in names if n.endswith(".dist-info/RECORD"))
        record = {r[0]: r for r in csv.reader(io.StringIO(z.read(record_name).decode("utf-8"))) if r}
        assert set(record) == set(names), "RECORD must list exactly the wheel's files"
        for n in names:
            if n == record_name:
                continue
            algo, _, digest = record[n][1].partition("=")
            raw = hashlib.new(algo, z.read(n)).digest()
            assert base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == digest, n
            assert int(record[n][2]) == len(z.read(n)), n

        for n in names:
            suffix = Path(n).suffix
            assert suffix not in _FORBIDDEN_SUFFIXES, f"native or startup code in {n}"
            if n in _DECLARED_SCRIPTS.get(entry["name"], set()):
                assert z.read(n).startswith(b"#!"), n
                continue
            assert ".data/" not in n, f"undeclared install-time file {n}"
            assert suffix in _ALLOWED_SUFFIXES or ".dist-info/" in n, n
        if entry["name"] not in _NO_UPSTREAM_LICENCE:
            assert any(".dist-info/" in n and "LICENSE" in n for n in names), "keep the licence in dist-info"
