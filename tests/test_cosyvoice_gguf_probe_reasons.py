"""#3980 -- every CosyVoice GGUF probe failure names its cause and falls back."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from engines.cosyvoice_gguf import bootstrap


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """A fake runtime binary whose behaviour comes from FAKE_* env vars."""
    if os.name == "nt":
        pytest.skip("shebang fake runtime is POSIX-only")
    cli = tmp_path / "cosyvoice-cli"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "time.sleep(float(os.environ.get('FAKE_SLEEP', '0')))\n"
        "sys.stdout.write(os.environ.get('FAKE_OUT', ''))\n"
        "sys.stderr.write(os.environ.get('FAKE_ERR', ''))\n"
        "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(bootstrap.CLI_ENV, str(cli))
    monkeypatch.delenv(bootstrap.BACKEND_ENV, raising=False)
    bootstrap._PROBE_CACHE.clear()
    return cli


def test_probe_success_path_unchanged(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_OUT", "Vulkan0 - NVIDIA GeForce GTX 1080 Ti\nCPU - i5\n")
    selected, fallback = bootstrap.selected_backend()
    assert (selected.name, selected.target, fallback) == ("Vulkan0", "vulkan", None)


def test_nonzero_exit_with_a_device_list_is_still_usable(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_OUT", "Vulkan0 - GTX 1080 Ti\n")
    monkeypatch.setenv("FAKE_EXIT", "3")
    assert bootstrap.selected_backend()[0].name == "Vulkan0"


def test_crash_names_exit_code_and_stderr_then_falls_back_to_cpu(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "7")
    monkeypatch.setenv("FAKE_ERR", "ggml_vulkan: vkCreateInstance failed\n")
    with pytest.raises(RuntimeError, match=r"exit code 7.*vkCreateInstance"):
        bootstrap.probe_backends()
    selected, fallback = bootstrap.selected_backend()
    assert selected.name == "CPU" and selected.target == "cpu"
    assert "exit code 7" in fallback and "CPU path" in fallback


def test_unrecognised_output_says_so(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_OUT", "usage: cosyvoice-cli [options]\n")
    with pytest.raises(RuntimeError, match="no recognisable device list.*usage"):
        bootstrap.probe_backends()


def test_timeout_names_the_limit_and_a_slow_first_probe_is_cached(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_OUT", "CPU - i5\n")
    monkeypatch.setenv("FAKE_SLEEP", "2")
    monkeypatch.setattr(bootstrap, "PROBE_TIMEOUT_S", 0.3)
    with pytest.raises(RuntimeError, match=r"timed out after 0\.3 s"):
        bootstrap.probe_backends()
    monkeypatch.setattr(bootstrap, "PROBE_TIMEOUT_S", 20)
    with pytest.raises(RuntimeError, match="timed out"):  # the failed verdict is cached too
        bootstrap.probe_backends()
    assert bootstrap.probe_backends(refresh=True)[0].name == "CPU"  # explicit re-check succeeds
    monkeypatch.setenv("FAKE_SLEEP", "30")
    assert bootstrap.probe_backends()[0].name == "CPU"  # cached: no second slow run


def test_unstartable_runtime_is_reported(tmp_path, monkeypatch):
    broken = tmp_path / "cosyvoice-cli"
    broken.write_bytes(b"not an executable")
    broken.chmod(0o755)
    bootstrap._PROBE_CACHE.clear()
    with pytest.raises(RuntimeError, match="could not be started"):
        bootstrap.probe_backends(broken)


def test_explicit_backend_never_falls_back_and_cpu_skips_the_probe(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "7")
    monkeypatch.setenv(bootstrap.BACKEND_ENV, "Vulkan0")
    with pytest.raises(RuntimeError, match="exit code 7"):
        bootstrap.selected_backend()
    monkeypatch.setenv(bootstrap.BACKEND_ENV, "cpu")
    assert bootstrap.selected_backend()[0].name == "CPU"


def test_runtime_profile_reports_cpu_fallback_when_the_probe_fails(fake_cli, monkeypatch):
    import engines.cosyvoice_gguf as engine_module

    monkeypatch.setenv("FAKE_EXIT", "7")
    profile = engine_module.CosyVoiceGGUFBackend.runtime_compute_profile(None)
    assert profile["routing_status"] == "cpu_fallback"
    assert "exit code 7" in profile["routing_reason"]


def test_missing_binary_reasons_name_the_path_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv(bootstrap.BIN_ENV, raising=False)
    monkeypatch.delenv(bootstrap.CLI_ENV, raising=False)
    monkeypatch.setenv(bootstrap.DIR_ENV, str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="does not exist") as raised:
        bootstrap.resolve_server()
    assert str(tmp_path / "nope") in str(raised.value)
    monkeypatch.setenv(bootstrap.DIR_ENV, str(tmp_path))
    with pytest.raises(RuntimeError, match="is not in"):
        bootstrap.resolve_cli()
    release = tmp_path / "bin" / "Release"
    release.mkdir(parents=True)
    name = "cosyvoice-cli.exe" if os.name == "nt" else "cosyvoice-cli"
    (release / name).write_bytes(b"x")
    assert bootstrap.resolve_cli() == (release / name).resolve()


def test_snapshot_miss_names_the_cache_and_uses_the_recorded_revision(tmp_path, monkeypatch):
    monkeypatch.delenv(bootstrap.MODEL_ENV, raising=False)
    monkeypatch.delenv(bootstrap.FRONTEND_ENV, raising=False)
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr("services.hf_cache_repair.hf_cache_home", lambda: str(tmp_path))
    with pytest.raises(RuntimeError, match=r"local Hugging Face cache.*OSError") as raised:
        bootstrap.resolve_model()
    assert str(tmp_path) in str(raised.value)
    recorded = "a" * 40
    repo = tmp_path / ("models--" + bootstrap.HF_MODEL_REPO.replace("/", "--"))
    snap = repo / "snapshots" / recorded
    snap.mkdir(parents=True)
    (repo / "voicestudio-revision").write_text(recorded)
    (snap / bootstrap.MODEL_FILE).write_bytes(b"m")
    assert bootstrap.resolve_model() == snap / bootstrap.MODEL_FILE
    with pytest.raises(RuntimeError, match=r"frontend ONNX files missing.*campplus"):
        bootstrap.frontend_dir()


def test_no_failure_reason_collapses_to_the_generic_public_line(tmp_path, monkeypatch):
    from api.public_engine_metadata import _UNAVAILABLE, public_backends

    monkeypatch.delenv(bootstrap.BIN_ENV, raising=False)
    monkeypatch.setenv(bootstrap.DIR_ENV, str(tmp_path))
    with pytest.raises(RuntimeError) as raised:
        bootstrap.resolve_server()
    reasons = [
        str(raised.value),
        "CosyVoice GGUF model files were not found in the local Hugging Face cache x (pinned: OSError).",
        "CosyVoice GGUF model files: frontend ONNX files missing from x: a",
        "CosyVoice GGUF model files: model.gguf is missing from x",
        "CosyVoice GGUF native runtime: server exited during startup (exit code 1, backend CPU)",
        "CosyVoice GGUF native runtime: backend probe of cli timed out after 120 s",
    ]
    for reason in reasons:
        public = public_backends([{"id": "cosyvoice-gguf", "reason": reason}])[0]["reason"]
        assert public != _UNAVAILABLE, reason


def test_failed_probe_is_cached_until_the_binary_changes_or_refresh(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "7")
    with pytest.raises(RuntimeError, match="exit code 7"):
        bootstrap.probe_backends()
    monkeypatch.setenv("FAKE_EXIT", "0")
    monkeypatch.setenv("FAKE_OUT", "CPU - i5\n")
    with pytest.raises(RuntimeError, match="exit code 7"):  # cached failure, no re-probe
        bootstrap.probe_backends()
    assert bootstrap.probe_backends(refresh=True)[0].name == "CPU"


def test_server_log_tail_reads_only_the_end(tmp_path, monkeypatch):
    from engines.cosyvoice_gguf import process

    managed = tmp_path / "managed"
    managed.mkdir()
    (tmp_path / "server.log").write_bytes(b"line\n" * 1_000_000 + b"vkCreateInstance failed\n")
    monkeypatch.setattr(bootstrap, "managed_dir", lambda: managed)
    monkeypatch.setattr(process.Path, "read_bytes", lambda self: pytest.fail("reads the whole log"))
    reason = process.NativeServer()._exit_reason()
    assert "vkCreateInstance failed" in reason


def test_bad_model_override_says_correct_it_not_reinstall():
    from api.public_engine_metadata import _public_unavailable_reason

    for text in (
        "CosyVoice GGUF model files: OMNIVOICE_COSYVOICE_GGUF_MODEL points to a missing model file: /x",
        "CosyVoice GGUF model files: OMNIVOICE_COSYVOICE_GGUF_FRONTEND is missing frontend files: a.onnx",
    ):
        reason = _public_unavailable_reason(text)
        assert "Correct or remove" in reason and "Reinstall" not in reason
    assert "Reinstall" in _public_unavailable_reason("CosyVoice GGUF model files: m.gguf is missing from /c")


def test_failed_probe_verdict_expires_but_stays_cached_until_then(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "7")
    with pytest.raises(RuntimeError, match="exit code 7"):
        bootstrap.probe_backends()
    monkeypatch.setenv("FAKE_EXIT", "0")
    monkeypatch.setenv("FAKE_OUT", "CPU - i5\n")
    with pytest.raises(RuntimeError, match="exit code 7"):  # still inside the TTL
        bootstrap.probe_backends()
    (key,) = bootstrap._PROBE_CACHE
    message, _ = bootstrap._PROBE_CACHE[key]
    bootstrap._PROBE_CACHE[key] = (message, 0.0)  # TTL elapsed
    assert bootstrap.probe_backends()[0].name == "CPU"


def test_expired_probe_verdict_survives_a_concurrent_expiry(fake_cli, monkeypatch):
    """Two threads can read the same expired failure; the second removal must not raise."""
    monkeypatch.setenv("FAKE_EXIT", "7")
    with pytest.raises(RuntimeError, match="exit code 7"):
        bootstrap.probe_backends()
    (key,) = bootstrap._PROBE_CACHE
    message, _ = bootstrap._PROBE_CACHE[key]

    class RacedCache(dict):
        def get(self, k, default=None):
            value = super().get(k, default)
            super().pop(k, None)  # the other thread expired it first
            return value

        def __getitem__(self, k):
            value = super().__getitem__(k)
            super().pop(k, None)
            return value

    monkeypatch.setattr(bootstrap, "_PROBE_CACHE", RacedCache({key: (message, 0.0)}))
    monkeypatch.setenv("FAKE_EXIT", "0")
    monkeypatch.setenv("FAKE_OUT", "CPU - i5\n")
    assert bootstrap.probe_backends()[0].name == "CPU"


def test_exit_reason_ignores_log_lines_from_an_earlier_launch(tmp_path, monkeypatch):
    from engines.cosyvoice_gguf.process import NativeServer

    monkeypatch.setattr(bootstrap, "managed_dir", lambda: tmp_path / "managed")
    log = tmp_path / "server.log"
    log.write_bytes(b"previous launch: vkCreateInstance failed\n")
    server = NativeServer()
    server.log_handle = log.open("ab")
    server._log_start = server.log_handle.tell()
    assert "vkCreateInstance" not in server._exit_reason()
    server.log_handle.write(b"this launch: missing DLL\n")
    reason = server._exit_reason()
    assert "missing DLL" in reason and "vkCreateInstance" not in reason
    server.log_handle.close()
