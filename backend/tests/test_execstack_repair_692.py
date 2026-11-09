"""#692 — clear PF_X on CTranslate2's PT_GNU_STACK in place; probes degrade."""
from __future__ import annotations

import os
import struct
import sys
import threading

import pytest

from core import execstack

PT_LOAD = 1
PT_GNU_STACK = 0x6474E551


def make_elf(path, *, bits=64, endian="<", flags=0x7, with_stack=True):
    e = endian
    phs = [(PT_LOAD, 0x5)]
    if with_stack:
        phs.append((PT_GNU_STACK, flags))
    ident = b"\x7fELF" + bytes([1 if bits == 32 else 2, 1 if e == "<" else 2, 1]) + b"\0" * 9
    if bits == 64:
        hsz, ent = 64, 56
        hdr = bytearray(ident + b"\0" * (hsz - 16))
        struct.pack_into(e + "Q", hdr, 0x20, hsz)
        struct.pack_into(e + "HH", hdr, 0x36, ent, len(phs))
        body = b"".join(struct.pack(e + "II", t, f) + b"\0" * (ent - 8) for t, f in phs)
    else:
        hsz, ent = 52, 32
        hdr = bytearray(ident + b"\0" * (hsz - 16))
        struct.pack_into(e + "I", hdr, 0x1C, hsz)
        struct.pack_into(e + "HH", hdr, 0x2A, ent, len(phs))
        body = b"".join(
            struct.pack(e + "I", t) + b"\0" * 20 + struct.pack(e + "I", f) + b"\0" * 4
            for t, f in phs
        )
    data = bytes(hdr) + body
    path.write_bytes(data)
    return data


@pytest.mark.parametrize("bits", [32, 64])
@pytest.mark.parametrize("endian", ["<", ">"])
def test_clear_flips_only_pf_x(tmp_path, bits, endian):
    p = tmp_path / "libx.so"
    before = make_elf(p, bits=bits, endian=endian, flags=0x7)
    assert execstack.has_execstack(p) is True
    changed, detail = execstack.clear_execstack(p)
    assert changed and "cleared" in detail
    after = p.read_bytes()
    assert execstack.has_execstack(p) is False
    diff = [i for i in range(len(before)) if before[i] != after[i]]
    assert len(diff) == 1  # only the PF_X bit (0x7 -> 0x6) changed
    assert before[diff[0]] ^ after[diff[0]] == 1


def test_already_clear_is_left_alone(tmp_path):
    p = tmp_path / "libx.so"
    before = make_elf(p, flags=0x6)
    changed, detail = execstack.clear_execstack(p)
    assert not changed and "non-executable" in detail
    assert p.read_bytes() == before


def test_no_segment(tmp_path):
    p = tmp_path / "libx.so"
    make_elf(p, with_stack=False)
    assert execstack.has_execstack(p) is None
    changed, detail = execstack.clear_execstack(p)
    assert not changed and "no PT_GNU_STACK" in detail


def test_non_elf_and_missing(tmp_path):
    p = tmp_path / "junk.so"
    p.write_bytes(b"not an elf at all" * 10)
    assert execstack.has_execstack(p) is None
    assert execstack.has_execstack(tmp_path / "missing.so") is None
    changed, detail = execstack.clear_execstack(tmp_path / "missing.so")
    assert not changed and "FileNotFoundError" in detail


@pytest.mark.parametrize("bits", [32, 64])
def test_truncated_elf_never_raises(tmp_path, bits):
    p = tmp_path / "full.so"
    data = make_elf(p, bits=bits)
    for n in range(0, len(data)):
        q = tmp_path / "cut.so"
        q.write_bytes(data[:n])
        assert execstack.has_execstack(q) is None
        assert execstack.clear_execstack(q)[0] is False


def _fake_lib_dir(monkeypatch, tmp_path, names=("libctranslate2.so.4",), **kw):
    for n in names:
        make_elf(tmp_path / n, **kw)
    monkeypatch.setattr(execstack, "ctranslate2_library_paths",
                        lambda: sorted(str(tmp_path / n) for n in names if (tmp_path / n).exists()))


@pytest.fixture(autouse=True)
def _linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from core.windows_rocm import runtime
    monkeypatch.setattr(runtime, "initialize_dll_search", lambda: None)


def test_ensure_repairs_then_ok(tmp_path, monkeypatch):
    _fake_lib_dir(monkeypatch, tmp_path)
    ok, detail = execstack.ensure_ctranslate2_loadable()
    assert ok and "repaired" in detail
    assert execstack.has_execstack(tmp_path / "libctranslate2.so.4") is False
    assert execstack.ensure_ctranslate2_loadable()[0]


def test_unrepairable_is_blocked_and_rechecked(tmp_path, monkeypatch):
    _fake_lib_dir(monkeypatch, tmp_path)
    real = execstack.clear_execstack
    monkeypatch.setattr(execstack, "clear_execstack",
                        lambda p: (False, f"{p} not writable (PermissionError)"))
    ok, detail = execstack.ensure_ctranslate2_loadable()
    assert not ok and "patchelf --clear-execstack" in detail and "3.12" in detail
    assert not execstack.ensure_ctranslate2_loadable()[0]
    monkeypatch.setattr(execstack, "clear_execstack", real)
    assert execstack.ensure_ctranslate2_loadable()[0]


def test_noop_off_linux(tmp_path, monkeypatch):
    _fake_lib_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    ok, _ = execstack.ensure_ctranslate2_loadable()
    assert ok
    assert execstack.has_execstack(tmp_path / "libctranslate2.so.4") is True


def test_library_installed_after_absent_probe(tmp_path, monkeypatch):
    names = ("libctranslate2.so.4",)
    monkeypatch.setattr(execstack, "ctranslate2_library_paths",
                        lambda: [str(tmp_path / n) for n in names if (tmp_path / n).exists()])
    ok, detail = execstack.ensure_ctranslate2_loadable()
    assert ok and "no CTranslate2" in detail
    make_elf(tmp_path / names[0])
    ok, detail = execstack.ensure_ctranslate2_loadable()
    assert ok and "repaired" in detail


def test_concurrent_repairs(tmp_path, monkeypatch):
    _fake_lib_dir(monkeypatch, tmp_path, names=("libctranslate2.so.4", "libctranslate2-x.so"))
    results = []
    threads = [threading.Thread(target=lambda: results.append(execstack.ensure_ctranslate2_loadable()))
               for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 8 and all(r[0] for r in results)


def test_locking_follows_host_os_name(tmp_path, monkeypatch):
    p = tmp_path / "libx.so"
    make_elf(p)
    fcntl = pytest.importorskip("fcntl")  # Unix-only module
    seen = []
    monkeypatch.setattr(fcntl, "flock", lambda fd, op: seen.append(op))
    monkeypatch.setattr(sys, "platform", "win32")  # must not disable locking
    assert execstack.clear_execstack(p)[0]
    assert fcntl.LOCK_EX in seen


def test_library_paths_without_package(monkeypatch):
    import importlib.util as iu
    monkeypatch.setattr(iu, "find_spec", lambda n: (_ for _ in ()).throw(ValueError("x")))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert execstack.ctranslate2_library_paths() == []


def test_library_paths_finds_siblings(tmp_path, monkeypatch):
    import importlib.util as iu
    from types import SimpleNamespace
    pkg = tmp_path / "ctranslate2"
    libs = tmp_path / "ctranslate2.libs"
    pkg.mkdir()
    libs.mkdir()
    (pkg / "libctranslate2.so.4").write_bytes(b"")
    (libs / "libctranslate2-abc.so.4.4.0").write_bytes(b"")
    (pkg / "other.so").write_bytes(b"")
    monkeypatch.setattr(iu, "find_spec",
                        lambda n: SimpleNamespace(submodule_search_locations=[str(pkg)], origin=None))
    got = execstack.ctranslate2_library_paths()
    assert [os.path.basename(g) for g in got] == ["libctranslate2.so.4", "libctranslate2-abc.so.4.4.0"]


# ── callers ──────────────────────────────────────────────────────────────

def test_asr_probes_report_unavailable_when_repair_impossible(monkeypatch):
    from services import asr_backend as ab
    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", lambda: (False, "blocked"))
    from core.windows_rocm import ct2, pipelines
    monkeypatch.setattr(ct2, "availability_status", lambda: None)
    monkeypatch.setattr(pipelines, "pipeline_error", lambda *a, **k: None)
    ok, msg = ab.FasterWhisperBackend.is_available()
    assert not ok and msg == "faster-whisper cannot load CTranslate2: blocked"
    ok, msg = ab.WhisperXBackend.is_available()
    assert not ok and msg == "whisperx cannot load CTranslate2: blocked"


def test_broken_repair_never_blocks_asr(monkeypatch):
    from services import asr_backend as ab

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", boom)
    assert ab._ctranslate2_execstack_ok()[0] is True


def test_argos_probe_module_and_survives_oserror(monkeypatch):
    from services import translation_engines as te
    assert te.REGISTRY["argos"]["probe_module"] == "argostranslate.translate"
    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", lambda: (True, "ok"))
    import importlib

    def bad(name, *a, **k):
        raise OSError("cannot enable executable stack")

    monkeypatch.setattr(importlib, "import_module", bad)
    ok, msg = te._probe(te.REGISTRY["argos"])
    assert not ok and "OSError" in msg


def test_argos_probe_reports_blocked_repair(monkeypatch):
    from services import translation_engines as te
    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", lambda: (False, "blocked"))
    assert te._probe(te.REGISTRY["argos"]) == (False, "blocked")


def test_sidecar_probe_repairs_before_import(monkeypatch):
    from services import subprocess_asr
    order = []
    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable",
                        lambda: order.append("repair") or (False, "blocked"))
    from core.windows_rocm import ct2
    monkeypatch.setattr(ct2, "availability_status", lambda: None)
    cls = next(c for c in vars(subprocess_asr).values()
               if isinstance(c, type) and "is_available" in vars(c)
               and "faster-whisper cannot load" in __import__("inspect").getsource(c.is_available))
    ok, msg = cls.is_available()
    assert order == ["repair"] and not ok and "blocked" in msg
