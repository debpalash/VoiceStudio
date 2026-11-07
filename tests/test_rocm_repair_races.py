"""A repair must not overwrite a newer manual sidecar selection."""
from dataclasses import replace

from services import sidecar_install as si


def test_manual_selection_during_repair_is_not_overwritten(monkeypatch, tmp_path):
    spec = replace(si.get_spec("moss-tts-nano"), requires_install_marker=False,
                   weights_repo_id=None, installed_probe=lambda: True)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.delenv(spec.env_var, raising=False)
    monkeypatch.setattr(si, "managed_root", lambda _: tmp_path)
    monkeypatch.setattr(si, "_source_present", lambda *_: True)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: ["rocm"])
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda _: True)
    original = tmp_path / spec.checkout_dirname
    py = si._venv_python(original / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("working", encoding="utf-8")
    for name in ("_step_preflight", "_step_fetch_source", "_step_create_venv",
                 "_step_install_deps", "_step_fetch_weights"):
        monkeypatch.setattr(si, name, lambda *_: None)
    external = str(tmp_path.parent / "chosen-after-repair-start")
    monkeypatch.setattr(si, "_step_verify", lambda *_: monkeypatch.setenv(spec.env_var, external))
    promoted = []
    monkeypatch.setattr(si, "_step_persist", lambda *_: promoted.append(True))
    job = si._new_job(spec.engine_id)
    job["_preserve_existing"] = True
    job["_expected_env"] = None
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert promoted == []
    assert __import__("os").environ[spec.env_var] == external
    assert py.read_text(encoding="utf-8") == "working"


def test_generation_directory_symlink_cannot_escape_managed_root(monkeypatch, tmp_path):
    import pytest
    from services import sidecar_generations
    spec = si.get_spec("moss-tts-nano")
    root, external = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    external.mkdir()
    try:
        (root / ".voicestudio-generations").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable on this host")
    with pytest.raises(ValueError):
        sidecar_generations.new_generation(spec, root)
    assert list(external.iterdir()) == []


def test_persist_failure_keeps_previous_selection_and_retains_stage(monkeypatch, tmp_path):
    import os

    from core import prefs
    spec = replace(si.get_spec("moss-tts-nano"), requires_install_marker=False,
                   weights_repo_id=None, installed_probe=lambda: True, invalidate=lambda: None)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.delenv(spec.env_var, raising=False)
    monkeypatch.setattr(si, "managed_root", lambda _: tmp_path)
    monkeypatch.setattr(si, "_source_present", lambda *_: True)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: ["rocm"])
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda path: (path / "hip").is_file())
    original = tmp_path / spec.checkout_dirname
    old_py = si._venv_python(original / ".venv")
    old_py.parent.mkdir(parents=True)
    old_py.write_text("working", encoding="utf-8")
    for name in ("_step_preflight", "_step_fetch_source", "_step_verify", "_step_fetch_weights"):
        monkeypatch.setattr(si, name, lambda *_: None)
    def build(staged, _job):
        venv = si.managed_checkout(staged) / ".venv"
        py = si._venv_python(venv)
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("staged", encoding="utf-8")
        (venv / "hip").touch()
    monkeypatch.setattr(si, "_step_create_venv", build)
    monkeypatch.setattr(si, "_step_install_deps", lambda *_: None)
    saved = {}
    def fail_save(key, value):
        saved[key] = value
        raise OSError("preferences failed after writing")
    monkeypatch.setattr(prefs, "set_", fail_save)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert spec.env_var not in os.environ
    assert old_py.read_text(encoding="utf-8") == "working"
    saved_checkout = __import__("pathlib").Path(saved[f"env.{spec.env_var}"])
    assert saved_checkout.is_dir()
    assert si.managed_checkout(spec) == original


def test_externally_pinned_path_is_not_silently_replaced(monkeypatch, tmp_path):
    from core import prefs
    spec = replace(si.get_spec("moss-tts-nano"), requires_install_marker=False,
                   weights_repo_id=None, installed_probe=lambda: True)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.setattr(si, "managed_root", lambda _: tmp_path)
    monkeypatch.setattr(si, "_source_present", lambda *_: True)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: ["rocm"])
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda _: False)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda _: True)
    checkout = tmp_path / spec.checkout_dirname
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("working", encoding="utf-8")
    monkeypatch.setenv(spec.env_var, str(checkout))
    job = si._new_job(spec.engine_id)
    job["_preserve_existing"] = True
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert py.read_text(encoding="utf-8") == "working"
    assert not (tmp_path / ".voicestudio-generations").exists()


def test_external_checkout_link_aborts_repair_without_touching_user_data(monkeypatch, tmp_path):
    import pytest
    spec = replace(si.get_spec("moss-tts-nano"), requires_install_marker=False,
                   weights_repo_id=None, installed_probe=lambda: True)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.delenv(spec.env_var, raising=False)
    monkeypatch.setattr(si, "managed_root", lambda _: tmp_path / "root")
    monkeypatch.setattr(si, "_source_present", lambda *_: True)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: ["rocm"])
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda _: False)
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("working", encoding="utf-8")
    external = tmp_path / "outside"
    external.mkdir()
    (external / "keep").write_text("safe", encoding="utf-8")
    try:
        (checkout / "external").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable on this host")
    monkeypatch.setattr(si, "_step_preflight", lambda *_: None)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert "manual" in job["remediation"].lower()
    assert py.read_text(encoding="utf-8") == "working"
    assert (external / "keep").read_text(encoding="utf-8") == "safe"
    assert not list((tmp_path / "root" / ".voicestudio-generations").iterdir())
