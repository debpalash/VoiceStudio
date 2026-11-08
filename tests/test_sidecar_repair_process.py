"""A repaired sidecar switches processes without interrupting active work."""
from dataclasses import replace
from pathlib import Path
import sys

from core import prefs
from services import sidecar_install
from services.subprocess_backend import SubprocessBackend


class EchoBackend(SubprocessBackend):
    id = "_repair_echo"
    display_name = "Repair echo"

    @property
    def sample_rate(self):
        return 24000

    @property
    def supported_languages(self):
        return ["multi"]

    @classmethod
    def is_available(cls):
        return True, "ready"

    @classmethod
    def venv_python(cls):
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls):
        return Path(__file__).resolve().parents[1] / "backend/engines/_echo/main.py"


def test_repair_keeps_busy_sidecar_until_next_request(monkeypatch, tmp_path):
    spec = replace(sidecar_install.get_spec("voxcpm2"), engine_id=EchoBackend.id,
                   env_var="OMNIVOICE_TEST_REPAIR_DIR", invalidate=lambda: None)
    monkeypatch.setattr(sidecar_install, "managed_checkout", lambda _: tmp_path)
    monkeypatch.setattr(prefs, "set_", lambda *_: None)
    monkeypatch.delenv(spec.env_var, raising=False)
    backend = EchoBackend()
    try:
        with backend._lock:
            backend._spawn()
            original = backend._proc
            sidecar_install._persist(spec)
            assert backend._proc is original
            assert original.poll() is None
        with backend._lock:
            backend._spawn()
            assert backend._proc is not original
            assert original.poll() is not None
            assert backend._proc.poll() is None
    finally:
        backend.shutdown()


def test_promotion_during_spawn_is_not_lost(monkeypatch, tmp_path):
    spec = replace(sidecar_install.get_spec("voxcpm2"), engine_id=EchoBackend.id,
                   env_var="OMNIVOICE_TEST_REPAIR_DIR", invalidate=lambda: None)
    monkeypatch.setattr(sidecar_install, "managed_checkout", lambda _: tmp_path)
    monkeypatch.setattr(prefs, "set_", lambda *_: None)
    monkeypatch.delenv(spec.env_var, raising=False)
    original_resolver = EchoBackend.venv_python
    promoted = []

    def resolve(cls):
        if not promoted:
            promoted.append(True)
            sidecar_install._persist(spec)
        return original_resolver()

    monkeypatch.setattr(EchoBackend, "venv_python", classmethod(resolve))
    backend = EchoBackend()
    try:
        with backend._lock:
            backend._spawn()
            original = backend._proc
            backend._spawn()
            assert backend._proc is not original
            assert original.poll() is not None
    finally:
        backend.shutdown()
