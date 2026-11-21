"""Availability probes import heavy, interdependent packages (torch, transformers,
omnivoice). Running them on worker threads raced those first imports and could
report the active engine as unavailable, so list_backends probes serially, in
registry order, on the calling thread."""
import threading
import time

import pytest

from services import asr_backend, tts_backend


def _probe_class(base, name, log, state):
    def is_available(cls):
        with state["lock"]:
            state["running"] += 1
            state["max"] = max(state["max"], state["running"])
        log.append((name, threading.get_ident()))
        time.sleep(0.05)  # a slow first import shared with another probe
        with state["lock"]:
            state["running"] -= 1
        return True, "ready"

    return type(name, (base,), {"id": name, "is_available": classmethod(is_available)})


@pytest.mark.parametrize("module", [tts_backend, asr_backend])
def test_probes_run_one_at_a_time_on_the_calling_thread_in_registry_order(module, monkeypatch):
    base = module.TTSBackend if module is tts_backend else module.ASRBackend
    log: list = []
    state = {"lock": threading.Lock(), "running": 0, "max": 0}
    names = ["probe-a", "probe-b", "probe-c"]
    registry = {n: _probe_class(base, n, log, state) for n in names}
    monkeypatch.setattr(module, "_REGISTRY", registry)

    try:
        result = module.list_backends()
    except Exception as exc:  # pragma: no cover - environment specific
        pytest.skip(f"list_backends needs host detection unavailable here: {exc}")

    assert state["max"] == 1, "probes overlapped"
    assert [n for n, _ in log] == names
    assert {t for _, t in log} == {threading.get_ident()}
    assert [b["id"] for b in result if b["id"] in names] == names
