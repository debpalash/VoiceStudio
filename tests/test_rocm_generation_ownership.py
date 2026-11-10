"""Managed generations retain uninstall safety and user-clone protection."""
from dataclasses import replace

from services import sidecar_generations
from services import sidecar_install as si


def test_completed_generation_remains_managed_and_uninstallable(monkeypatch, tmp_path):
    from core import prefs
    root = tmp_path / "engine-root"
    spec = replace(si.get_spec("moss-tts-nano"), invalidate=lambda: None)
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.setattr(si, "managed_root", lambda _: root)
    monkeypatch.setattr(prefs, "get", lambda *_: None)
    staged = sidecar_generations.new_generation(spec, root)
    checkout = root / staged.checkout_dirname
    checkout.mkdir()
    monkeypatch.setenv(spec.env_var, str(checkout))
    assert si.managed_checkout(spec) == checkout
    assert si.uninstall(spec.engine_id)["status"] == "uninstalled"
    assert not root.exists()


def test_unmarked_generation_path_is_not_treated_as_managed(monkeypatch, tmp_path):
    root = tmp_path / "engine-root"
    spec = si.get_spec("moss-tts-nano")
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.setattr(si, "managed_root", lambda _: root)
    external = root / ".voicestudio-generations" / ("a" * 32) / spec.checkout_dirname
    external.mkdir(parents=True)
    monkeypatch.setenv(spec.env_var, str(external))
    assert si.uninstall(spec.engine_id)["status"] == "not_managed"
    assert external.is_dir()


def test_copy_preserves_checkout_data_without_live_environment(tmp_path):
    root = tmp_path / "engine"
    source = root / "old"
    source.mkdir(parents=True)
    (source / ".venv").mkdir()
    (source / ".venv" / "old-wheel").write_text("old", encoding="utf-8")
    (source / ".voicestudio_install_complete").write_text("old", encoding="utf-8")
    (source / "settings.json").write_text("custom", encoding="utf-8")
    destination = root / "new"
    sidecar_generations.copy_preserved_checkout(source, destination, root)
    assert (destination / "settings.json").read_text(encoding="utf-8") == "custom"
    assert not (destination / ".venv").exists()
    assert not (destination / ".voicestudio_install_complete").exists()
    assert (source / ".venv" / "old-wheel").is_file()


def test_copy_rejects_external_links_without_touching_target(tmp_path):
    import pytest
    root, outside = tmp_path / "engine", tmp_path / "outside"
    source = root / "old"
    source.mkdir(parents=True)
    outside.mkdir()
    (outside / "keep").write_text("safe", encoding="utf-8")
    try:
        (source / "external").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable on this host")
    with pytest.raises(ValueError):
        sidecar_generations.copy_preserved_checkout(source, root / "new", root)
    assert (outside / "keep").read_text(encoding="utf-8") == "safe"
    assert not (root / "new").exists()


def test_completed_marker_sidecar_remains_installed_during_upgrade_offer(monkeypatch, tmp_path):
    spec = replace(si.get_spec("cosyvoice"), weights_repo_id=None, install_revision="")
    monkeypatch.setitem(si.SPECS, spec.engine_id, spec)
    monkeypatch.delenv(spec.env_var, raising=False)
    monkeypatch.setattr(si, "managed_root", lambda _: tmp_path)
    monkeypatch.setattr(si, "_source_present", lambda *_: True)
    monkeypatch.setattr(si, "_spec_rocm_torch_args", lambda _: ["rocm"])
    monkeypatch.setattr(si, "_venv_torch_is_rocm", lambda _: False)
    checkout = tmp_path / spec.checkout_dirname
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("working", encoding="utf-8")
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("done", encoding="utf-8")
    status = si.get_status(spec.engine_id)
    assert status["installed"] is True
    assert status["repair_available"] is True
