"""PocketTTS first-use gate and model-free sidecar integration (#1442)."""
from __future__ import annotations

import re
import sys
import types

import pytest


def _backend_cls():
    """Resolve app code at test runtime; sys.modules-isolating tests may reload it."""
    from engines.pockettts import PocketTTSBackend

    return PocketTTSBackend


def _workflow_paths(workflows):
    return sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml")))


STUB_SIDECAR = r'''
import base64, json, struct, sys

def send(obj):
    body = json.dumps(obj, separators=(",", ":")).encode()
    sys.stdout.buffer.write(struct.pack("!I", len(body)) + body)
    sys.stdout.buffer.flush()

def recv():
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        return None
    length = struct.unpack("!I", header)[0]
    return json.loads(sys.stdin.buffer.read(length))

send({"op": "ready", "engine": "pockettts", "sample_rate": 24000})
while True:
    message = recv()
    if message is None or message.get("op") == "shutdown":
        break
    if message.get("op") == "ping":
        send({"op": "pong", "vram_mb": 0.0})
    elif message.get("op") == "synthesize":
        # Prove engine-specific kwargs cross the real subprocess wire.
        assert message.get("language") == "fr"
        assert message.get("ref_audio") == "/tmp/reference.wav"
        pcm = struct.pack("<4h", 0, 1000, -1000, 0)
        send({"op": "progress", "stage": "loading_model", "percent": 50})
        send({"op": "audio", "audio_pcm_b64": base64.b64encode(pcm).decode(),
              "sample_rate": 24000, "n_samples": 4})
'''


def test_license_gate_fails_closed_then_allows_engine(monkeypatch, mock_settings_store):
    monkeypatch.setitem(sys.modules, "pocket_tts", types.ModuleType("pocket_tts"))
    mock_settings_store.pop("pockettts", None)

    ok, reason = _backend_cls().is_available()
    assert ok is False
    assert "license not accepted" in reason.lower()
    assert "Model Catalogue" in reason

    mock_settings_store["pockettts"] = True
    assert _backend_cls().is_available() == (True, "ready (CPU-only)")


def test_pockettts_is_a_pinned_optional_extra():
    import tomllib
    from pathlib import Path

    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8")
    )
    assert project["project"]["optional-dependencies"]["pockettts"] == [
        "pocket-tts==2.1.0 ; sys_platform != 'darwin' or platform_machine != 'x86_64'"
    ]


def test_pockettts_reports_the_intel_mac_wheel_gap(monkeypatch):
    monkeypatch.setattr("engines.pockettts.sys.platform", "darwin")
    monkeypatch.setattr("engines.pockettts.platform.machine", lambda: "x86_64")
    ok, reason = _backend_cls().is_available()
    assert ok is False
    assert "Intel Macs" in reason
    assert "PyTorch" in reason


def test_direct_construction_rejects_intel_mac(monkeypatch, mock_settings_store):
    mock_settings_store["pockettts"] = True
    monkeypatch.setattr("engines.pockettts.sys.platform", "darwin")
    monkeypatch.setattr("engines.pockettts.platform.machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="Intel Macs"):
        _backend_cls()()


def test_ci_verifies_intel_mac_as_the_documented_remote_only_host():
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(
        "utf-8"
    )
    assert "label: macOS Intel\n            backend_supported: false" in workflow
    assert "name: Verify the documented Intel Mac contract" in workflow
    # What must hold is that the pockettts extra is installed ONLY on the
    # backend-supported legs — not how the sync is invoked. Pinning the exact
    # command made an unrelated CI hardening change (routing every sync through
    # scripts/uv-sync-retry.sh) look like a broken Intel-Mac contract.
    assert re.search(
        r"if: matrix\.backend_supported\n        run: .*--extra pockettts", workflow
    ), "the pockettts extra must be installed only on backend_supported legs"
    assert re.search(
        r"if: matrix\.backend_supported\n        run: uv run .*pytest tests/smoke/", workflow
    ), "the smoke suite must run only on backend_supported legs"
    assert "HF_HUB_CACHE: ${{ runner.temp }}/pockettts-empty-hf-cache" in workflow


@pytest.mark.parametrize(
    ("ref", "valid"),
    [
        ("v1", True),
        ("v1.6.3", True),
        ("0123456789abcdef0123456789abcdef01234567", True),
        ("latest", False),
        ("main", False),
        ("release", False),
        ("v1-beta", False),
        ("0123456", False),
    ],
)
def test_cache_apt_action_pin_policy(ref, valid):
    pattern = r"(?:v\d+(?:\.\d+){0,2}|[0-9a-f]{40})"
    assert (re.fullmatch(pattern, ref) is not None) is valid


def test_cache_apt_action_is_pinned_in_every_workflow():
    from pathlib import Path

    workflows = Path(__file__).resolve().parents[1] / ".github/workflows"
    paths = _workflow_paths(workflows)
    assert paths
    for path in paths:
        text = path.read_text("utf-8")
        refs = re.findall(r"awalsh128/cache-apt-pkgs-action@([^\s#]+)", text)
        for ref in refs:
            assert re.fullmatch(r"v\d+(?:\.\d+){0,2}|[0-9a-f]{40}", ref), (
                path,
                ref,
            )


def test_workflow_pin_scan_includes_both_yaml_extensions(tmp_path):
    (tmp_path / "one.yml").touch()
    (tmp_path / "two.yaml").touch()
    assert [path.name for path in _workflow_paths(tmp_path)] == ["one.yml", "two.yaml"]


def test_every_locale_discloses_hugging_face_model_access():
    import json
    from pathlib import Path

    locales = Path(__file__).resolve().parents[1] / "electron/src/shared/i18n/locales"
    paths = sorted(locales.glob("*.json"))
    assert len(paths) == 21
    for path in paths:
        strings = json.loads(path.read_text("utf-8"))
        footer = strings["license"]["pockettts_footer"]
        assert "Hugging Face" in footer, path


@pytest.mark.parametrize(
    "reason",
    [
        "Cannot access gated repo for model kyutai/pocket-tts",
        "PocketTTS requires you to share your contact information",
        "Kyutai Pocket TTS access agreement has not been accepted",
    ],
)
def test_gated_weight_failures_are_typed_and_actionable(reason):
    from core.failure import build_failure, classify

    assert classify(reason) == "POCKETTTS_GATED_WEIGHTS"
    failure = build_failure(reason, stage="model_load", include_diagnostic=False)
    assert failure["docs_topic"] == "POCKETTTS_GATED_WEIGHTS"
    assert "huggingface.co/kyutai/pocket-tts" in failure["hint"]
    assert "HF_TOKEN" in failure["hint"]


def test_generic_gated_model_still_uses_existing_pyannote_class():
    from core.failure import classify

    assert classify("gated model license not accepted") == "PYANNOTE_LICENSE_REQUIRED"


def test_license_gate_fails_closed_when_settings_read_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "pocket_tts", types.ModuleType("pocket_tts"))
    from services import settings_store
    monkeypatch.setattr(settings_store, "get_license_accepted", lambda _eid: (_ for _ in ()).throw(OSError("db unavailable")))
    assert _backend_cls().is_available()[0] is False


def test_direct_backend_construction_cannot_bypass_license(mock_settings_store):
    mock_settings_store.pop("pockettts", None)
    with pytest.raises(RuntimeError, match="license not accepted"):
        _backend_cls()()


def test_cached_backend_stops_synthesis_after_license_revocation(mock_settings_store):
    mock_settings_store["pockettts"] = True
    backend = _backend_cls()()
    try:
        mock_settings_store["pockettts"] = False
        with pytest.raises(RuntimeError, match="license not accepted"):
            backend.generate("must not reach the sidecar")
    finally:
        backend.shutdown()


def test_queued_synthesis_rechecks_license_after_acquiring_lock(
    monkeypatch, mock_settings_store
):
    mock_settings_store["pockettts"] = True
    backend = _backend_cls()()

    class RevokingLock:
        def __enter__(self):
            mock_settings_store["pockettts"] = False

        def __exit__(self, *_args):
            return False

    backend._lock = RevokingLock()
    monkeypatch.setattr(
        "services.model_manager.running_on_gpu_pool", lambda: True
    )
    monkeypatch.setattr(
        backend, "_spawn", lambda: pytest.fail("revoked request reached sidecar")
    )

    with pytest.raises(RuntimeError, match="license not accepted"):
        backend.generate("queued before revocation")


def test_stub_sidecar_roundtrip_is_model_free_and_forwards_voice_inputs(
    tmp_path, monkeypatch, mock_settings_store
):
    mock_settings_store["pockettts"] = True
    stub = tmp_path / "pockettts_stub.py"
    stub.write_text(STUB_SIDECAR, encoding="utf-8")
    backend_cls = _backend_cls()
    monkeypatch.setattr(backend_cls, "sidecar_script", classmethod(lambda cls: stub))
    # Use this interpreter while retaining the engine's real override surface.
    monkeypatch.setattr(backend_cls, "venv_python", classmethod(lambda cls: __import__("pathlib").Path(sys.executable)))

    backend = backend_cls()
    try:
        audio = backend.generate(
            "bonjour", language="fr", ref_audio="/tmp/reference.wav"
        )
        assert tuple(audio.shape) == (1, 4)
        assert audio[0, 1].item() == pytest.approx(1000 / 32768.0)
    finally:
        backend.shutdown()


def test_license_api_accepts_pockettts_and_rejects_unknown(settings_mod, mock_settings_store):
    body = settings_mod._LicenseAcceptBody(engine_id=" PocketTTS ", accepted=True)
    assert settings_mod.post_license_acceptance(body) == {
        "ok": True, "engine_id": "pockettts", "accepted": True
    }
    assert mock_settings_store["pockettts"] is True

    with pytest.raises(Exception) as exc:
        settings_mod.post_license_acceptance(
            settings_mod._LicenseAcceptBody(engine_id="unknown", accepted=True)
        )
    assert getattr(exc.value, "status_code", None) == 400


@pytest.fixture
def settings_mod():
    import importlib
    return importlib.import_module("api.routers.settings")


def test_prefers_the_venv_its_one_click_install_made(monkeypatch, tmp_path, mock_settings_store):
    """Its own venv when the installer made one; otherwise the app's
    interpreter, where `uv sync --extra pockettts` installs it."""
    from pathlib import Path

    from services.sidecar_install import _INSTALL_COMPLETE_MARKER, _venv_python

    mock_settings_store["pockettts"] = True
    monkeypatch.delenv("OMNIVOICE_POCKETTTS_DIR", raising=False)
    assert _backend_cls().venv_python() == Path(sys.executable)

    py = _venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    (tmp_path / _INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_POCKETTTS_DIR", str(tmp_path))
    assert _backend_cls().venv_python() == py
    # Available without pocket_tts importable in the app's own environment.
    monkeypatch.setitem(sys.modules, "pocket_tts", None)
    if _backend_cls()._platform_error() is None:
        assert _backend_cls().is_available() == (True, "ready (CPU-only)")
