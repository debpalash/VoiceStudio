"""The glibc guard behind the Snap base (#3879) and its CI wiring."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_glibc_floor", ROOT / "scripts" / "check_glibc_floor.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

OBJDUMP = """
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.2.5) memcpy
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.34)  pthread_create
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.39)  __isoc23_strtol
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.9)   pipe2
0000000000000000      DF *UND*  0000000000000000 (GLIBC_PRIVATE) __libc_stack_end
0000000000000000      DF *UND*  0000000000000000 (GCC_3.0)  _Unwind_Resume
"""


def test_picks_highest_version_numerically_not_lexically():
    assert guard.required_glibc(OBJDUMP) == (2, 39)
    assert guard.required_glibc("(GLIBC_2.9) a\n(GLIBC_2.35) b") == (2, 35)


def test_ignores_private_and_non_glibc_versions():
    assert guard.required_glibc("(GLIBC_PRIVATE) x\n(GCC_3.0) y") is None


def test_check_flags_only_newer_than_the_base(tmp_path, monkeypatch):
    elf = tmp_path / "helper"
    elf.write_bytes(b"\x7fELF" + b"\0" * 8)
    (tmp_path / "notes.txt").write_text("not an elf")
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": OBJDUMP, "returncode": 0})())
    assert guard.check([tmp_path], "2.35") == [f"{elf}: needs GLIBC_2.39 (max 2.35)"]
    assert guard.check([tmp_path], "2.39") == []


def test_default_ceiling_is_the_snap_base():
    assert guard.DEFAULT_MAX == "2.35"


def test_linux_packaging_builds_on_jammy_and_runs_the_guard():
    # ubuntu-22.04 ships glibc 2.35, the same as the Snap base core22.
    release = (ROOT / ".github" / "workflows" / "electron-release.yml").read_text(encoding="utf-8")
    snap = (ROOT / ".github" / "workflows" / "snap-store.yml").read_text(encoding="utf-8")
    assert "- runner: ubuntu-22.04\n            platform: linux" in release
    assert "runs-on: ubuntu-22.04" in snap.split("package:")[1].split("upload:")[0]
    for text in (release, snap):
        assert "scripts/check_glibc_floor.py" in text


def test_check_fails_when_objdump_cannot_inspect_an_elf(tmp_path, monkeypatch):
    elf = tmp_path / "helper"
    elf.write_bytes(b"\x7fELF" + b"\0" * 8)
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "", "returncode": 1})())
    assert guard.check([tmp_path], "2.35") == [f"{elf}: objdump could not inspect it (exit 1)"]
