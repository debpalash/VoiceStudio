"""Resident ``tts-server`` mode of the OmniVoice GGUF engine (#3654).

A per-request ``omnivoice-tts`` spawn reloads the LM + codec every time
(~3.5 s). When a verified ``tts-server`` binary is bundled, one loopback
server per quant pair stays loaded between requests; every case it cannot
serve keeps today's spawn path. Resident mode is opt-in (off by default), so
the rig opts in with ``OMNIVOICE_GGUF_RESIDENT=1``. These tests drive a real child process
(``fake_gguf_tts_server.py``, speaking the upstream API) through the real
process owner, and a stubbed CLI spawn.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path

import pytest

FAKE_SERVER = Path(__file__).with_name("fake_gguf_tts_server.py")
_ELF = b"\x7fELF" + b"\x00" * 16


def _write_wav(path: Path, n: int = 72_000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(b"".join(
            struct.pack("<h", int(1000 * math.sin(i / 10))) for i in range(n)
        ))


class _Rig:
    def __init__(self, monkeypatch, root: Path):
        from engines.omnivoice_gguf import backend as mod
        from engines.omnivoice_gguf import resident

        self.mod, self.resident, self.root = mod, resident, root
        self.state = root / "state"
        self.state.mkdir()
        monkeypatch.setenv("FAKE_TTS_STATE", str(self.state))
        monkeypatch.setenv(resident.RESIDENT_ENV, "1")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.cli = bin_dir / "omnivoice-tts-stub"
        self.cli.write_bytes(_ELF)
        self.cli.chmod(0o755)
        monkeypatch.setattr(mod, "_binary_path", lambda slug=None: self.cli)
        monkeypatch.setattr(mod, "_load_checksum_manifest", lambda: {})
        self.server_bin = resident.server_binary_path()
        self.server_bin.write_bytes(_ELF)
        self.server_bin.chmod(0o755)
        monkeypatch.setattr(resident, "_log_path", lambda: root / "logs" / "tts-server.log")
        monkeypatch.setattr(resident, "_READY_TIMEOUT_S", 30.0)
        resident._STATUS_CACHE.clear()
        real_spawn = resident.spawn_owned
        self.server_argvs: list = []

        def spawn(args, **kw):
            self.server_argvs.append(list(args))
            return real_spawn([sys.executable, str(FAKE_SERVER), *args[1:]], **kw)

        monkeypatch.setattr(resident, "spawn_owned", spawn)

        self.cli_argvs: list = []

        def fake_run(argv, **_kw):
            if argv[0] != str(self.cli):  # e.g. the macOS quarantine probe
                return subprocess.CompletedProcess(argv, 1, "", "")
            self.cli_argvs.append(list(argv))
            _write_wav(Path(argv[argv.index("-o") + 1]))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        self.instance = mod._make_backend_class()()
        self.base, self.codec = root / "omnivoice-base-Q8_0.gguf", root / "omnivoice-tokenizer-Q8_0.gguf"
        self.base.write_bytes(b"0")
        self.codec.write_bytes(b"0")
        monkeypatch.setattr(
            self.instance, "_resolve_quant_paths",
            lambda: (self.base, self.codec, {"base": self.base.name, "tokenizer": self.codec.name}),
        )

    def flag(self, name: str, on: bool = True) -> None:
        path = self.state / name
        if on:
            path.write_text("1", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def events(self, kind: str) -> list[dict]:
        log = self.state / "events.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
        return [r for r in rows if r["kind"] == kind]

    @property
    def server(self):
        return self.instance._resident


@pytest.fixture
def rig(monkeypatch, tmp_path):
    r = _Rig(monkeypatch, tmp_path)
    yield r
    r.instance.unload()


def _arg(argv, flag):
    return argv[argv.index(flag) + 1]


# ── gating ────────────────────────────────────────────────────────────────


def test_without_a_server_binary_every_request_spawns_the_cli(rig):
    rig.server_bin.unlink()
    rig.resident._STATUS_CACHE.clear()
    out = rig.instance.generate("hello")
    assert out.shape == (1, 72_000)
    assert len(rig.cli_argvs) == 1
    assert rig.server_argvs == [] and rig.events("start") == []
    assert rig.resident.server_status() == (False, "no tts-server binary is bundled")


def test_an_installer_manifest_must_list_the_server(rig, monkeypatch):
    monkeypatch.setattr(rig.mod, "_load_checksum_manifest", lambda: {rig.cli.name: "0" * 64})
    rig.resident._STATUS_CACHE.clear()
    ok, why = rig.resident.server_status()
    assert not ok and "not listed" in why
    rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 1 and rig.server_argvs == []

    digest = rig.mod._sha256_of_file(rig.server_bin)
    monkeypatch.setattr(rig.mod, "_load_checksum_manifest", lambda: {rig.server_bin.name: digest})
    rig.resident._STATUS_CACHE.clear()
    assert rig.resident.server_status() == (True, "ready")


def test_the_setting_off_uses_the_cli_and_stops_a_running_server(rig, monkeypatch):
    rig.instance.generate("hello")
    proc = rig.server.process
    assert proc.poll() is None
    monkeypatch.setenv(rig.resident.RESIDENT_ENV, "0")
    rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 1
    assert proc.poll() is not None and not rig.instance.execution_evidence_loaded()


def test_resident_mode_is_opt_in_and_the_api_toggle_controls_it(rig, monkeypatch):
    """Off by default: upstream tts-server has no API key and sends CORS *."""
    from api.routers import settings as router
    from core import prefs

    monkeypatch.delenv(rig.resident.RESIDENT_ENV)
    prefs.delete(rig.resident.RESIDENT_PREF)
    try:
        assert rig.resident.resident_enabled() is False
        assert router.get_gguf_resident() == {"enabled": False, "env_pinned": False, "available": True}
        rig.instance.generate("hello")
        assert len(rig.cli_argvs) == 1 and rig.server_argvs == [] and rig.events("start") == []

        assert router.set_gguf_resident(router._GgufResidentBody(enabled=True))["enabled"] is True
        rig.instance.generate("hello")
        assert len(rig.cli_argvs) == 1 and len(rig.events("start")) == 1
        proc = rig.server.process

        state = router.set_gguf_resident(router._GgufResidentBody(enabled=False))
        assert state["enabled"] is False
        assert proc.poll() is not None
        rig.instance.generate("hello")
        assert len(rig.cli_argvs) == 2
    finally:
        prefs.delete(rig.resident.RESIDENT_PREF)


@pytest.mark.parametrize("value, enabled", [("1", True), ("on", True), ("0", False), ("bogus", False)])
def test_the_env_override_opts_in_only_on_a_truthy_value(monkeypatch, value, enabled):
    from engines.omnivoice_gguf import resident

    monkeypatch.setenv(resident.RESIDENT_ENV, value)
    assert resident.resident_enabled() is enabled


# ── lifecycle ─────────────────────────────────────────────────────────────


def test_starts_lazily_on_loopback_and_is_reused_across_requests(rig):
    assert rig.server is None and rig.events("start") == []
    first = rig.instance.generate("hello\n", language="fr", seed=7, instruct="whisper")
    second = rig.instance.generate("again")
    assert first.shape == second.shape == (1, 2400)
    assert rig.cli_argvs == []
    assert len(rig.events("start")) == 1
    argv = rig.server_argvs[0]
    assert argv[0] == str(rig.server_bin)
    assert _arg(argv, "--host") == "127.0.0.1"
    assert _arg(argv, "--model") == str(rig.base) and _arg(argv, "--codec") == str(rig.codec)
    speech = [e["body"] for e in rig.events("speech")]
    assert speech[0] == {"input": "hello", "response_format": "wav", "language": "French",
                         "instructions": "whisper", "seed": 7}
    assert speech[1] == {"input": "again", "response_format": "wav"}
    assert rig.instance.execution_evidence_loaded()


def test_a_cloned_voice_is_registered_once_per_prompt(rig, tmp_path):
    clip = tmp_path / "ref.wav"
    _write_wav(clip, 2400)
    for _ in range(2):
        rig.instance.generate("x", ref_audio=str(clip), ref_text="hello there")
    voices = rig.events("voice")
    assert len(voices) == 1 and voices[0]["ref_text"] == "hello there"
    assert {e["body"]["voice"] for e in rig.events("speech")} == {voices[0]["name"]}
    rig.instance.generate("x", ref_audio=str(clip), ref_text="another transcript")
    assert len(rig.events("voice")) == 2
    assert rig.cli_argvs == []


def test_a_new_quant_pair_replaces_the_server(rig, tmp_path, monkeypatch):
    rig.instance.generate("hello")
    old = rig.server.process
    other = tmp_path / "omnivoice-base-BF16.gguf"
    other.write_bytes(b"0")
    monkeypatch.setattr(rig.instance, "_resolve_quant_paths",
                        lambda: (other, rig.codec, {"base": other.name, "tokenizer": rig.codec.name}))
    rig.instance.generate("hello")
    assert old.poll() is not None
    assert _arg(rig.server_argvs[-1], "--model") == str(other)


def test_a_crash_gets_one_restart_and_re_registers_the_voice(rig, tmp_path):
    clip = tmp_path / "ref.wav"
    _write_wav(clip, 2400)
    rig.instance.generate("x", ref_audio=str(clip), ref_text="hi")
    rig.flag("crash_next_speech")
    out = rig.instance.generate("x", ref_audio=str(clip), ref_text="hi")
    assert out.shape == (1, 2400)
    assert len(rig.events("start")) == 2
    assert len(rig.events("voice")) == 2
    assert rig.cli_argvs == []


def test_a_body_cut_off_mid_wav_takes_the_crash_path(rig, caplog):
    rig.instance.generate("hello")
    rig.flag("truncate_next_speech")
    with caplog.at_level("WARNING", logger="omnivoice.gguf"):
        out = rig.instance.generate("hello")
    assert out.shape == (1, 2400) and rig.cli_argvs == []  # the restart served it
    assert len(rig.events("start")) == 2
    assert "restarting it once" in caplog.text
    rig.flag("crash_every_speech")
    rig.flag("truncate_next_speech")
    rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 1  # a second failure spawns per request


def test_a_server_that_died_between_requests_is_restarted(rig):
    rig.instance.generate("hello")
    rig.server.process.kill()
    rig.server.process.wait(5)
    rig.instance.generate("hello")
    assert len(rig.events("start")) == 2 and rig.cli_argvs == []


def test_a_second_crash_falls_back_to_the_cli(rig, caplog):
    rig.flag("crash_every_speech")
    with caplog.at_level("WARNING", logger="omnivoice.gguf"):
        out = rig.instance.generate("hello")
    assert out.shape == (1, 72_000)
    assert len(rig.events("start")) == 2 and len(rig.cli_argvs) == 1
    assert "failed again after a restart" in caplog.text


def test_a_server_that_cannot_start_falls_back_and_is_not_retried_until_unload(rig, caplog):
    rig.flag("exit_at_start")
    with caplog.at_level("WARNING", logger="omnivoice.gguf"):
        rig.instance.generate("hello")
        rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 2
    assert len(rig.events("start")) == 1
    assert "resident mode unavailable" in caplog.text
    rig.flag("exit_at_start", on=False)
    rig.instance.unload()
    rig.instance.generate("hello")
    assert len(rig.events("start")) == 2 and len(rig.cli_argvs) == 2


def test_a_server_without_the_voice_registry_falls_back(rig):
    rig.flag("no_voice_registry")
    rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 1
    assert not rig.instance.execution_evidence_loaded()


def test_a_refused_request_falls_back_and_keeps_the_server(rig):
    rig.instance.generate("hello")
    proc = rig.server.process
    rig.flag("reject_speech")
    rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 1 and proc.poll() is None
    rig.flag("reject_speech", on=False)
    rig.instance.generate("hello")
    assert len(rig.cli_argvs) == 1 and len(rig.events("start")) == 1


def test_a_timeout_stops_the_server_and_raises(rig, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_GGUF_GENERATE_TIMEOUT_S", "1")
    rig.flag("slow_speech")
    with pytest.raises(RuntimeError, match="timed out"):
        rig.instance.generate("hello")
    assert rig.cli_argvs == [] and not rig.instance.execution_evidence_loaded()


@pytest.mark.parametrize("controls", [
    {"duration": 2.0},
    {"chunk_duration": 0},
    {"chunk_threshold": 50},
    {"denoise": False},
    {"preprocess_prompt": False},
])
def test_controls_the_server_lacks_take_the_cli(rig, controls):
    rig.instance.generate("hello", **controls)
    assert len(rig.cli_argvs) == 1 and rig.events("start") == []


def test_a_reference_without_a_transcript_takes_the_cli(rig, tmp_path):
    clip = tmp_path / "ref.wav"
    _write_wav(clip, 2400)
    rig.instance.generate("x", ref_audio=str(clip))
    assert len(rig.cli_argvs) == 1 and rig.events("start") == []


# ── release ───────────────────────────────────────────────────────────────


def test_unload_frees_the_process_and_is_idempotent(rig):
    rig.instance.generate("hello")
    proc = rig.server.process
    rig.instance.unload()
    rig.instance.unload()
    assert proc.poll() is not None
    assert not rig.instance.execution_evidence_loaded()


def test_idle_release_stops_the_server(rig, monkeypatch):
    from services import tts_backend

    rig.instance.generate("hello")
    proc = rig.server.process
    cls = type(rig.instance)
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {cls: rig.instance})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {cls: 0.0})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    assert tts_backend.release_idle_engines(60, now=1000.0) == [cls.id]
    assert proc.poll() is not None


def test_the_idle_worker_sweep_stops_the_active_instances_server(rig, monkeypatch):
    from services import model_manager, tts_backend

    rig.instance.generate("hello")
    proc = rig.server.process
    monkeypatch.setattr(tts_backend, "_active_instance", rig.instance)
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    model_manager._release_idle_side_models(3600)
    assert proc.poll() is None  # used a moment ago: not idle yet
    rig.instance._resident_used -= 7200
    with rig.server.lock:  # a request in flight (another thread) is never interrupted
        sweep = threading.Thread(target=model_manager._release_idle_side_models, args=(3600,))
        sweep.start()
        sweep.join()
        assert proc.poll() is None
    model_manager._release_idle_side_models(3600)
    assert proc.poll() is not None and not rig.instance.execution_evidence_loaded()


def test_the_memory_controls_see_and_unload_the_server(rig, monkeypatch):
    import asyncio

    from services import model_lifecycle, tts_backend

    rig.instance.generate("hello")
    proc = rig.server.process
    monkeypatch.setattr(tts_backend, "_active_instance", rig.instance)
    monkeypatch.setattr(tts_backend, "_active_instance_id", "omnivoice-gguf")
    ids = [m["id"] for m in model_lifecycle.list_loaded()["models"]]
    assert "active-engine:omnivoice-gguf" in ids
    asyncio.run(model_lifecycle.unload_all())
    assert proc.poll() is not None
    assert "active-engine:omnivoice-gguf" not in [
        m["id"] for m in model_lifecycle.list_loaded()["models"]
    ]
