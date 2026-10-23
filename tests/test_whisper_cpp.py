"""Unit tests for whisper.cpp ASR backend with Vulkan support (Phase 3)."""
from __future__ import annotations

from pathlib import Path

pytest_plugins = ["whisper_cpp_fixtures"]


def test_locate_binary_via_bin_env(tmp_path, monkeypatch, m):
    fake_bin = tmp_path / "whisper-cli.exe"
    fake_bin.write_text("binary content")
    fake_bin.chmod(0o755)
    monkeypatch.setenv(m.BIN_ENV, str(fake_bin))

    assert m.locate_binary() == fake_bin.resolve()

def test_locate_binary_via_dir_env(tmp_path, monkeypatch, m):
    monkeypatch.delenv(m.BIN_ENV, raising=False)
    fake_bin = tmp_path / "whisper-cli"
    fake_bin.write_text("binary content")
    fake_bin.chmod(0o755)
    monkeypatch.setenv(m.DIR_ENV, str(tmp_path))

    # Test candidate lookup
    monkeypatch.setattr("engines.whisper_cpp.bootstrap._CANDIDATE_NAMES", ("whisper-cli",))
    assert m.locate_binary() == fake_bin.resolve()

def test_locate_binary_via_path(monkeypatch, m):
    monkeypatch.delenv(m.BIN_ENV, raising=False)
    monkeypatch.delenv(m.DIR_ENV, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/whisper-cli")
    monkeypatch.setattr("engines.whisper_cpp.bootstrap._PKG_BIN_DIR", Path("/nonexistent/bin"))

    assert m.locate_binary() == Path("/usr/local/bin/whisper-cli").resolve()

def test_locate_binary_missing(monkeypatch, m):
    monkeypatch.delenv(m.BIN_ENV, raising=False)
    monkeypatch.delenv(m.DIR_ENV, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("engines.whisper_cpp.bootstrap._PKG_BIN_DIR", Path("/nonexistent/bin"))

    assert m.locate_binary() is None
    assert m.is_installed() is False

def test_resolve_model_direct_file(tmp_path, m):
    fake_model = tmp_path / "ggml-base.bin"
    fake_model.write_text("weights")

    assert m.resolve_model(str(fake_model)) == fake_model.resolve()

def test_resolve_model_in_user_cache(tmp_path, monkeypatch, m):
    cache_dir = tmp_path / ".cache" / "whisper"
    cache_dir.mkdir(parents=True)
    fake_model = cache_dir / "ggml-base.bin"
    fake_model.write_text("weights")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert m.resolve_model("base") == fake_model.resolve()

def test_is_available_verdicts(tmp_path, monkeypatch, m):
    # Neither binary nor model
    monkeypatch.setattr("engines.whisper_cpp.backend.locate_binary", lambda: None)
    monkeypatch.setattr("engines.whisper_cpp.backend.resolve_model", lambda: None)
    ok, msg = m.WhisperCppBackend.is_available()
    assert ok is False
    assert "binary not found" in msg

    # Binary found, model missing
    monkeypatch.setattr("engines.whisper_cpp.backend.locate_binary", lambda: tmp_path / "whisper-cli")
    ok, msg = m.WhisperCppBackend.is_available()
    assert ok is False
    assert "model not found" in msg

    # Both found
    monkeypatch.setattr("engines.whisper_cpp.backend.resolve_model", lambda: tmp_path / "ggml-base.bin")
    ok, msg = m.WhisperCppBackend.is_available()
    assert ok is True
    assert msg == "ok"

def test_gpu_compat_and_routing(m):
    assert m.WhisperCppBackend.gpu_compat == ("vulkan", "cuda", "mps", "cpu")
    assert m.WhisperCppBackend.id == "whisper-cpp"

    # A loader or torch CUDA capability cannot prove the installed CLI's providers.
    caps = m.HostCaps(family="cuda", available_families=("cuda", "cpu"), notes=())
    routing = m.WhisperCppBackend.runtime_compute_profile(caps)
    assert routing["effective_device"] == "cpu"
    assert "unverified" in routing["routing_reason"]
