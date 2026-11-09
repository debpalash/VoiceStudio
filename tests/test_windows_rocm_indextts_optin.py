"""Unknown IndexTTS metadata must preserve defaults and gate Windows ROCm opt-in."""

import sys

import pytest

MODES = [
    ("unresolved", None, None),
    ("absent", None, None),
    ("unreadable", None, None),
    ("probe_error", None, None),
    ("malformed", "hip =\n", "cpu_fallback"),
    ("cpu", "hip = None\n", "cpu_fallback"),
    ("hip", 'hip: str | None = "7.2.1"\n', "accelerated"),
]
HOSTS = [
    ("win32", None, "accelerated"),
    ("linux", None, "accelerated"),
    ("darwin", None, "accelerated"),
    ("win32", "auto", "accelerated"),
    ("linux", "auto", "accelerated"),
    ("darwin", "auto", "accelerated"),
    ("win32", " ROCm ", "cpu_fallback"),
    ("linux", "rocm", "accelerated"),
    ("darwin", "rocm", "accelerated"),
]


@pytest.mark.parametrize("platform,variant,unknown_status", HOSTS)
@pytest.mark.parametrize("state,metadata,known_status", MODES)
def test_sidecar_metadata_routing_preserves_defaults_and_explicit_optin(
    monkeypatch,
    tmp_path,
    platform,
    variant,
    unknown_status,
    state,
    metadata,
    known_status,
):
    from core.device_caps import HostCaps
    from engines.indextts import IndexTTS2Backend, bootstrap

    monkeypatch.setattr(sys, "platform", platform)
    if variant is None:
        monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    venv = tmp_path / ".venv"
    python = venv / "Scripts/python.exe"
    monkeypatch.setattr(bootstrap, "_resolved_python", None if state in {"unresolved", "probe_error"} else python)
    monkeypatch.setattr(bootstrap, "_probe_paths", lambda: ())
    if metadata is not None:
        version = venv / "Lib/site-packages/torch/version.py"
        version.parent.mkdir(parents=True)
        version.write_text(metadata, encoding="utf-8")
    if state == "unreadable":
        monkeypatch.setattr(
            "services.sidecar_install.venv_torch_hip", lambda path: (_ for _ in ()).throw(OSError("denied"))
        )
    if state == "probe_error":
        monkeypatch.setattr(bootstrap, "_probe_paths", lambda: (_ for _ in ()).throw(OSError("denied")))
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"), device_name="Fixture Radeon")
    row = IndexTTS2Backend.runtime_compute_profile(caps)
    expected = known_status or unknown_status
    assert row["routing_status"] == expected
    assert row["effective_device"] == ("rocm" if expected == "accelerated" else "cpu")
    assert ("rocm" in row["gpu_compat"]) is (expected == "accelerated")


def test_windows_opted_sidecar_respects_user_cpu_override(monkeypatch, tmp_path):
    from core.device_caps import HostCaps
    from engines.indextts import IndexTTS2Backend, bootstrap

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    monkeypatch.setattr(bootstrap, "_resolved_python", tmp_path / ".venv/Scripts/python.exe")
    monkeypatch.setattr("services.sidecar_install.venv_torch_hip", lambda path: True)
    caps = HostCaps(family="cpu", available_families=("rocm", "cpu"), requested_family="cpu")
    row = IndexTTS2Backend.runtime_compute_profile(caps)
    assert row["routing_status"] == "cpu_only"
    assert row["effective_device"] == "cpu"
