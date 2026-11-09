"""A comment-only requirements revision must not disable working CosyVoice installs."""
import importlib

import pytest


@pytest.mark.parametrize('needs_rocm,has_hip,expected', [
    (False, False, True), (False, True, True),
    (True, False, False), (True, True, True),
])
def test_previous_cosyvoice_marker_preserves_working_install(monkeypatch, tmp_path, needs_rocm, has_hip, expected):
    """Legacy installs remain usable; AMD repairs still require matching HIP."""
    si = importlib.import_module('services.sidecar_install')
    spec = si.SPECS['cosyvoice']
    python = si._venv_python(tmp_path / '.venv')
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(si, '_weights_present', lambda s: True)
    monkeypatch.setattr(si, '_user_managed_dir', lambda s: None)
    monkeypatch.setattr(si, '_safe_installed', lambda s: True)
    monkeypatch.setattr(si, '_source_present', lambda s, p: True)
    monkeypatch.setattr(si, 'managed_checkout', lambda s: tmp_path)
    monkeypatch.setattr(si, '_spec_rocm_torch_args', lambda s: [] if needs_rocm else None)
    monkeypatch.setattr(si, '_venv_torch_is_rocm', lambda p: has_hip)
    (tmp_path / si._INSTALL_COMPLETE_MARKER).write_text(
        f'{spec.probe_module}\nsecurity-floors-v4\n', encoding='utf-8')
    assert si._healthy(spec) is expected
