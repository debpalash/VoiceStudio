"""Prompt-cache cleanup and native process ownership contracts."""

from __future__ import annotations

from types import SimpleNamespace

from engines.cosyvoice_gguf import bootstrap, prompt_cache
from engines.cosyvoice_gguf.process import NativeServer


def test_failed_prompt_conversion_cleans_temporary_output(tmp_path, monkeypatch):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    monkeypatch.setattr(prompt_cache, "DATA_DIR", tmp_path)
    monkeypatch.setattr(prompt_cache.bootstrap, "frontend_dir", lambda: tmp_path)
    monkeypatch.setattr(
        prompt_cache.bootstrap, "resolve_cli", lambda: tmp_path / "cli.exe"
    )
    def failed_run(args, **kwargs):
        # A crashing encoder can leave partial output behind; cleanup must remove it.
        output = args[args.index("--prompt-speech-output") + 1]
        with open(output, "wb") as handle:
            handle.write(b"partial")
        return SimpleNamespace(returncode=1, stderr="failed", stdout="")

    monkeypatch.setattr(prompt_cache.subprocess, "run", failed_run)
    try:
        prompt_cache.cached_prompt(reference, "reference transcript")
    except RuntimeError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("failed prompt conversion was treated as successful")
    assert not list(
        (tmp_path / "engines" / bootstrap.ENGINE_ID / "prompts").glob("*.gguf")
    )


def test_native_server_uses_only_the_verified_loopback_backend(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.gguf"
    prompt.write_bytes(b"prompt")
    captured = []

    class Process:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

    native = NativeServer()
    monkeypatch.setattr(bootstrap, "resolve_server", lambda: tmp_path / "server.exe")
    monkeypatch.setattr(bootstrap, "resolve_cli", lambda: tmp_path / "cli.exe")
    monkeypatch.setattr(bootstrap, "resolve_model", lambda: tmp_path / "model.gguf")
    monkeypatch.setattr(
        bootstrap, "managed_dir", lambda: tmp_path / "engine" / "runtime"
    )
    monkeypatch.setattr(
        bootstrap,
        "selected_backend",
        lambda: (
            bootstrap.NativeBackend("Vulkan0", "AMD Radeon RX 6700 XT", "vulkan"),
            None,
        ),
    )
    monkeypatch.setattr(
        "engines.cosyvoice_gguf.process.spawn_owned",
        lambda args, **kw: captured.append(args) or Process(),
    )
    monkeypatch.setattr(native, "_wait_ready", lambda: None)
    native.ensure(prompt, "prompt-key")
    args = captured[0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--backend") + 1] == "Vulkan0"
    assert args[args.index("--voice-prompt") + 1] == f"mira={prompt}"
    assert "--api-key" in args and native.port
    native.stop()
