"""A ROCm upgrade must keep a working sidecar available on failure."""
from dataclasses import replace

import pytest
from services import sidecar_install as si


def _working_sidecar(monkeypatch, tmp_path):
    spec = replace(si.get_spec("moss-tts-nano"), requires_install_marker=False,
                   weights_repo_id=None, installed_probe=lambda: True)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.delenv(spec.env_var, raising=False)
    monkeypatch.setattr(si, "managed_root", lambda _: tmp_path)
    monkeypatch.setattr(si, "_source_present", lambda *_: True)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: ["rocm"])
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda _: False)
    checkout = tmp_path / spec.checkout_dirname
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("working interpreter", encoding="utf-8")
    return spec, checkout, py


def test_working_cpu_sidecar_stays_installed_and_exposes_repair(monkeypatch, tmp_path):
    spec, _, _ = _working_sidecar(monkeypatch, tmp_path)
    status = si.get_status(spec.engine_id)
    assert status["installed"] is True
    assert status["repair_available"] is True


def test_failed_upgrade_never_mutates_working_environment(monkeypatch, tmp_path):
    spec, original, py = _working_sidecar(monkeypatch, tmp_path)
    touched = []
    def no_op(_spec, _job):
        pass
    def fail_install(staged_spec, _job):
        destination = si.managed_checkout(staged_spec)
        touched.append(destination)
        (destination / ".venv").mkdir(parents=True, exist_ok=True)
        (destination / ".venv" / "partial").write_text("partial", encoding="utf-8")
        raise si._StepError("failed", "retry")
    for name in ("_step_preflight", "_step_fetch_source", "_step_create_venv"):
        monkeypatch.setattr(si, name, no_op)
    monkeypatch.setattr(si, "_step_install_deps", fail_install)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert touched and touched[0] != original
    assert py.read_text(encoding="utf-8") == "working interpreter"
    assert not (original / ".venv" / "partial").exists()
    assert si.get_status(spec.engine_id)["installed"] is True


def test_successful_upgrade_switches_only_after_verification(monkeypatch, tmp_path):
    from core import prefs
    spec, original, py = _working_sidecar(monkeypatch, tmp_path)
    (original / "custom-config.json").write_text("preserve", encoding="utf-8")
    spec = replace(spec, invalidate=lambda: None)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.setattr(prefs, "set_", lambda *_: None)
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda path: (path / "hip").is_file())
    events = []
    def prepare(staged_spec, _job):
        checkout = si.managed_checkout(staged_spec)
        assert checkout != original
        assert py.read_text(encoding="utf-8") == "working interpreter"
        events.append("prepared")
    def build(staged_spec, _job):
        venv = si.managed_checkout(staged_spec) / ".venv"
        target = si._venv_python(venv)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("new interpreter", encoding="utf-8")
        (venv / "hip").touch()
    def verify(staged_spec, _job):
        assert spec.env_var not in __import__("os").environ
        assert py.read_text(encoding="utf-8") == "working interpreter"
        assert (si.managed_checkout(staged_spec) / ".venv" / "hip").is_file()
        events.append("verified")
    for name in ("_step_preflight", "_step_fetch_source", "_step_create_venv", "_step_fetch_weights"):
        monkeypatch.setattr(si, name, prepare)
    monkeypatch.setattr(si, "_step_install_deps", build)
    monkeypatch.setattr(si, "_step_verify", verify)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "succeeded"
    assert "verified" in events
    selected = si.managed_checkout(spec)
    assert selected != original and selected.is_dir()
    assert (selected / "custom-config.json").read_text(encoding="utf-8") == "preserve"
    assert py.read_text(encoding="utf-8") == "working interpreter"
    status = si.get_status(spec.engine_id)
    assert status["installed"] is True and status["managed"] is True
    assert status["repair_available"] is False


def test_staging_creation_failure_is_reported_without_touching_old_sidecar(monkeypatch, tmp_path):
    from services import sidecar_generations
    spec, _, py = _working_sidecar(monkeypatch, tmp_path)
    def fail(*_):
        raise OSError("cannot create staging directory")
    monkeypatch.setattr(sidecar_generations, "new_generation", fail)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert py.read_text(encoding="utf-8") == "working interpreter"


def test_worker_never_overwrites_a_now_healthy_managed_sidecar(monkeypatch, tmp_path):
    spec, original, py = _working_sidecar(monkeypatch, tmp_path)
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda _: True)
    touched = []
    def fail_install(staged_spec, _job):
        touched.append(si.managed_checkout(staged_spec))
        raise si._StepError("failed", "retry")
    for name in ("_step_preflight", "_step_fetch_source", "_step_create_venv"):
        monkeypatch.setattr(si, name, lambda *_: None)
    monkeypatch.setattr(si, "_step_install_deps", fail_install)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert touched and touched[0] != original
    assert py.read_text(encoding="utf-8") == "working interpreter"


def test_worker_refuses_if_user_switches_to_external_install(monkeypatch, tmp_path):
    spec, _, py = _working_sidecar(monkeypatch, tmp_path)
    external = tmp_path.parent / "external-user-install"
    monkeypatch.setenv(spec.env_var, str(external))
    monkeypatch.setattr(si, "_step_install_deps", lambda *_: pytest.fail("must not install"))
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert py.read_text(encoding="utf-8") == "working interpreter"


def test_failed_real_dependency_step_keeps_original_venv_and_marker(monkeypatch, tmp_path):
    spec, original, py = _working_sidecar(monkeypatch, tmp_path)
    old_marker = original / si._INSTALL_COMPLETE_MARKER
    old_marker.write_text("old complete", encoding="utf-8")
    monkeypatch.setattr(si, "_step_preflight", lambda *_: None)
    monkeypatch.setattr(si, "_step_fetch_source", lambda *_: None)
    monkeypatch.setattr(si, "_locate_uv", lambda: "uv")
    monkeypatch.setattr(si, "uv_subprocess_env", lambda *_: {})
    def create_venv(staged_spec, _job):
        target = si._venv_python(si.managed_checkout(staged_spec) / ".venv")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("staged", encoding="utf-8")
    def fail_uv(_job, argv, **_kwargs):
        assert str(original) not in argv[argv.index("--python") + 1]
        return 1
    monkeypatch.setattr(si, "_step_create_venv", create_venv)
    monkeypatch.setattr(si, "_run_logged", fail_uv)
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert py.read_text(encoding="utf-8") == "working interpreter"
    assert old_marker.read_text(encoding="utf-8") == "old complete"
    assert si.get_status(spec.engine_id)["installed"] is True


def test_non_hip_upgrade_is_never_promoted(monkeypatch, tmp_path):
    spec, original, py = _working_sidecar(monkeypatch, tmp_path)
    promoted = []
    for name in ("_step_preflight", "_step_fetch_source", "_step_create_venv",
                 "_step_install_deps", "_step_verify", "_step_fetch_weights"):
        monkeypatch.setattr(si, name, lambda *_: None)
    monkeypatch.setattr(si, "_step_persist", lambda *_: promoted.append(True))
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    assert job["state"] == "failed"
    assert promoted == []
    assert py.read_text(encoding="utf-8") == "working interpreter"
    assert si.managed_checkout(spec) == original


def test_requested_repair_stays_staged_if_host_family_changes(monkeypatch, tmp_path):
    spec, original, py = _working_sidecar(monkeypatch, tmp_path)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: None)
    touched = []
    def fail_install(staged_spec, _job):
        touched.append(si.managed_checkout(staged_spec))
        raise si._StepError("failed", "retry")
    for name in ("_step_preflight", "_step_fetch_source", "_step_create_venv"):
        monkeypatch.setattr(si, name, lambda *_: None)
    monkeypatch.setattr(si, "_step_install_deps", fail_install)
    job = si._new_job(spec.engine_id)
    job["_preserve_existing"] = True
    si._run_install(spec, job)
    assert touched and touched[0] != original
    assert py.read_text(encoding="utf-8") == "working interpreter"
