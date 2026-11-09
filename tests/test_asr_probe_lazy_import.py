"""ASR availability probes must not import the ASR stacks into the API process (#3924).

Importing ``mlx_whisper`` and ``faster_whisper`` to answer "is this engine
usable?" cost about a second each and 166 MB of the backend's idle memory, even
with no speech-recognition model installed. The probes now prove the import in
a short-lived child (``core.import_probe``), so the answer stays the same,
including for a package whose native library refuses to load (#692), while the
packages stay out of ``sys.modules`` until a model is actually loaded.

Fake packages on a temporary ``sys.path`` stand in for the real ones, so the
tests behave the same on hosts with and without the real packages installed.
"""
from __future__ import annotations

import sys
import textwrap

import pytest

from core import import_probe
from services import asr_backend as ab


@pytest.fixture
def fake_packages(tmp_path, monkeypatch):
    """Install fake top-level packages: ``{name: body of __init__.py}``."""

    def install(**packages: str) -> None:
        for name, body in packages.items():
            pkg = tmp_path / name
            pkg.mkdir()
            (pkg / "__init__.py").write_text(textwrap.dedent(body))
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.syspath_prepend(str(tmp_path))

    import_probe.clear_cache()
    yield install
    import_probe.clear_cache()


@pytest.fixture
def probe_gates_open(monkeypatch):
    """Open the platform/native gates so each probe reaches its import check."""
    monkeypatch.setattr("core.device_caps.mlx_supported", lambda: (True, ""))
    monkeypatch.setattr("core.windows_rocm.ct2.availability_status", lambda: None)
    monkeypatch.setattr("core.windows_rocm.pipelines.pipeline_error", lambda: None)
    monkeypatch.setattr(ab, "_ctranslate2_execstack_ok", lambda: (True, "ok"))
    monkeypatch.setattr(ab, "_ctranslate2_cudnn_ok", lambda: (True, "ready"))


_PROBES = [
    (ab.MLXWhisperBackend, "mlx_whisper"),
    (ab.FasterWhisperBackend, "faster_whisper"),
    (ab.WhisperXBackend, "whisperx"),
    (ab.ParakeetMLXBackend, "parakeet_mlx"),
    (ab.FunASRBackend, "funasr"),
]


@pytest.mark.usefixtures("probe_gates_open")
@pytest.mark.parametrize(("backend", "module"), _PROBES, ids=[m for _, m in _PROBES])
def test_probe_reports_available_without_importing(fake_packages, backend, module):
    fake_packages(**{module: "LOADED = True\n"})
    ok, reason = backend.is_available()
    assert ok, reason
    assert module not in sys.modules, f"{backend.__name__}.is_available() imported {module}"


@pytest.mark.usefixtures("probe_gates_open")
def test_startup_capture_preflight_keeps_asr_stacks_out(fake_packages):
    """The background dictation preflight walks MLX → faster-whisper."""
    fake_packages(mlx_whisper="", faster_whisper="")
    ab._capture_whisper_repo()
    ab.FasterWhisperBackend.is_available()
    assert "mlx_whisper" not in sys.modules
    assert "faster_whisper" not in sys.modules


@pytest.mark.usefixtures("probe_gates_open")
def test_broken_native_library_is_still_reported_unavailable(fake_packages):
    """#692: a native library that refuses to load raises at import time."""
    fake_packages(
        faster_whisper='raise OSError("libctranslate2.so: cannot enable executable stack")\n',
        mlx_whisper='raise RuntimeError("metal device init failed")\n',
    )
    ok, reason = ab.FasterWhisperBackend.is_available()
    assert not ok
    assert reason == (
        "faster-whisper failed to load (OSError): libctranslate2.so: cannot enable executable stack"
    )
    ok, reason = ab.MLXWhisperBackend.is_available()
    assert (ok, reason) == (False, "mlx-whisper unavailable: metal device init failed")


@pytest.mark.usefixtures("probe_gates_open")
def test_missing_package_is_not_installed(fake_packages, monkeypatch):
    fake_packages()
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    ok, reason = ab.FasterWhisperBackend.is_available()
    assert not ok and reason.startswith("faster-whisper not installed:")


def test_already_imported_module_answers_without_a_child(monkeypatch):
    monkeypatch.setattr(import_probe, "_run_child", lambda *a: pytest.fail("spawned a child"))
    import_probe.ensure_importable("json")


def test_not_installed_answers_without_a_child(monkeypatch):
    monkeypatch.setattr(import_probe, "_run_child", lambda *a: pytest.fail("spawned a child"))
    with pytest.raises(ModuleNotFoundError) as info:
        import_probe.ensure_importable("voicestudio_no_such_package.sub")
    assert info.value.name == "voicestudio_no_such_package.sub"


def test_verdict_is_cached_per_installed_copy(fake_packages, monkeypatch):
    fake_packages(vs_probe_cached="")
    calls = []
    real = import_probe._run_child
    monkeypatch.setattr(import_probe, "_run_child", lambda *a: calls.append(a) or real(*a))
    import_probe.ensure_importable("vs_probe_cached")
    import_probe.ensure_importable("vs_probe_cached")
    assert len(calls) == 1
    assert "vs_probe_cached" not in sys.modules


def test_child_crash_is_a_failure(fake_packages):
    fake_packages(vs_probe_crash="import os\nos._exit(3)\n")
    with pytest.raises(RuntimeError, match="crashed the interpreter"):
        import_probe.ensure_importable("vs_probe_crash")


def test_no_child_falls_back_to_importing_here(fake_packages, monkeypatch):
    fake_packages(vs_probe_fallback="")
    monkeypatch.setattr(import_probe, "_run_child", lambda *a: None)
    import_probe.ensure_importable("vs_probe_fallback")
    assert "vs_probe_fallback" in sys.modules
