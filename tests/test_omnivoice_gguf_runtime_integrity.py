"""The native GGUF runtime loads only verified code, from bin/ (#3042).

With ``GGML_BACKEND_DL`` the runtime picks its CPU/Vulkan backend from
shared libraries at load time, scanning its own directory and the working
directory. So the libraries are verified like the executable, the process
runs with ``bin/`` as its working directory, and a bad device override is
reported as an engine error rather than a bare ``ValueError``.
"""
from __future__ import annotations

import hashlib
import subprocess
from types import SimpleNamespace

import pytest
from gguf_cache_fixtures import gguf_binary, gguf_cache, gguf_engine

__all__ = ["gguf_binary", "gguf_cache", "gguf_engine"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def runtime_dir(gguf_binary, gguf_engine, monkeypatch):
    """bin/ with the executable and one ggml library, plus a manifest setter."""
    lib = gguf_binary.parent / "libggml-cpu-haswell.so"
    lib.write_bytes(b"built-library")

    def manifest(entries):
        monkeypatch.setattr(gguf_engine, "_load_checksum_manifest", lambda: dict(entries))

    return gguf_binary, lib, manifest


def test_listed_library_with_the_built_bytes_is_ready(runtime_dir, gguf_engine):
    binary, lib, manifest = runtime_dir
    manifest({binary.name: _sha(binary.read_bytes()), lib.name: _sha(b"built-library")})
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is True, reason


def test_swapped_library_is_refused(runtime_dir, gguf_engine):
    binary, lib, manifest = runtime_dir
    manifest({binary.name: _sha(binary.read_bytes()), lib.name: _sha(b"built-library")})
    lib.write_bytes(b"swapped-library")
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False
    assert lib.name in reason


def test_library_of_another_platform_is_skipped(runtime_dir, gguf_engine):
    binary, _lib, manifest = runtime_dir
    manifest({
        binary.name: _sha(binary.read_bytes()),
        "libggml-cpu-haswell.so": _sha(b"built-library"),
        "ggml-vulkan.dll": _sha(b"windows build, not shipped here"),
    })
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is True, reason


def test_manifest_from_before_library_entries_still_works(runtime_dir, gguf_engine, caplog):
    """Older manifests list only the executable: warn, do not break the install."""
    binary, lib, manifest = runtime_dir
    manifest({binary.name: _sha(binary.read_bytes())})
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is True, reason
    assert lib.name in caplog.text


def _capture_run(monkeypatch, gguf_engine):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="usage", stderr="")

    monkeypatch.setattr(gguf_engine.subprocess, "run", fake_run)
    return seen


def test_probe_runs_inside_bin(gguf_binary, gguf_engine, monkeypatch):
    seen = _capture_run(monkeypatch, gguf_engine)
    gguf_engine.OmniVoiceGGUFBackend.probe_load(timeout=1)
    assert seen["cwd"] == str(gguf_binary.parent)


def test_generation_runs_inside_bin(gguf_binary, gguf_engine, monkeypatch):
    seen = _capture_run(monkeypatch, gguf_engine)
    monkeypatch.setattr("services.binary_preflight.validate_executable", lambda *a, **k: None)
    gguf_engine.OmniVoiceGGUFBackend()._run_subprocess([str(gguf_binary)], stdin_text="hi")
    assert seen["cwd"] == str(gguf_binary.parent)


@pytest.mark.parametrize("env", [
    {"OMNIVOICE_GGUF_BACKEND": "metal"},
    {"OMNIVOICE_GGUF_DEVICE": "1"},
    {"OMNIVOICE_GGUF_BACKEND": "vulkan", "OMNIVOICE_GGUF_DEVICE": "-1"},
])
def test_bad_device_override_is_an_engine_error(gguf_binary, gguf_engine, monkeypatch, env):
    """Generation and the probe expect RuntimeError; a bare ValueError escaped them."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    _capture_run(monkeypatch, gguf_engine)
    with pytest.raises(RuntimeError, match="OMNIVOICE_GGUF_BACKEND"):
        gguf_engine.OmniVoiceGGUFBackend.probe_load(timeout=1)
    assert gguf_engine.select_default_engine() == "omnivoice"


def test_bad_device_override_is_still_a_value_error():
    """Input validation callers keep catching ValueError."""
    from engines.omnivoice_gguf.compute import ComputeConfigError

    assert issubclass(ComputeConfigError, ValueError)
