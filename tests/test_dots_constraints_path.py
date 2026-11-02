"""Exercise uv's actual constraints parser without downloads or installations."""
import importlib
import shutil
import subprocess
import sys

import pytest


def test_managed_constraints_with_spaces_reach_uv(tmp_path):
    uv = shutil.which('uv')
    if not uv:
        pytest.skip('uv is not installed')
    si = importlib.import_module('services.sidecar_install')
    checkout = tmp_path / 'Application Support' / 'dots #1'
    constraints = checkout / 'constraints' / 'recommended.txt'
    constraints.parent.mkdir(parents=True)
    constraints.write_text('six==1.17.0\n')
    requirements = tmp_path / 'empty.txt'
    requirements.write_text('')
    args = [si._expand(arg, checkout) for arg in si.get_spec('dots-tts').install_args]
    argument = args[args.index('-c') + 1]
    result = subprocess.run(
        [uv, 'pip', 'install', '--python', sys.executable, '--offline',
         '--no-index', '--dry-run', '-c', argument, '-r', str(requirements)],
        capture_output=True, text=True, timeout=30,
        env=si.uv_subprocess_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_lazy_bootstrap_encodes_constraints_path(monkeypatch, tmp_path):
    bootstrap = importlib.import_module('engines.dots_tts.bootstrap')
    clone = tmp_path / 'Application Support' / 'dots #1'
    constraints = clone / 'constraints' / 'recommended.txt'
    constraints.parent.mkdir(parents=True)
    constraints.write_text('six==1.17.0\n')
    commands = []
    monkeypatch.setattr(bootstrap, '_locate_uv', lambda: 'uv')
    monkeypatch.setattr(bootstrap, '_ENGINES_VENV_DIR', tmp_path / 'venv')
    monkeypatch.setattr(bootstrap, '_venv_can_import_dots', lambda _: 'yes')
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda argv, **kw: commands.append(argv))
    bootstrap._bootstrap_engines_venv(clone)
    install = next(argv for argv in commands if argv[1:3] == ['pip', 'install'])
    assert install[install.index('-c') + 1] == constraints.as_uri()


def test_known_broken_pins_are_repaired_without_editing_upstream(tmp_path):
    repair = importlib.import_module('engines.dots_tts.install').compatible_constraints
    source = tmp_path / 'recommended.txt'
    original = 'gradio==6.17.0\ntransformers==4.57.0 # yanked\ntorch==2.8.0\n-r extras.txt\n'
    source.write_text(original)
    repaired = repair(source)
    assert source.read_text(encoding="utf-8") == original
    assert repaired.parent == source.parent  # relative includes still resolve
    assert repaired.read_text(encoding="utf-8") == original.replace('6.17.0', '6.17.3').replace('4.57.0', '4.57.1')
    assert repair(source).read_text(encoding="utf-8") == repaired.read_text(encoding="utf-8")  # retry is stable


def test_custom_or_newer_pins_are_not_overridden(tmp_path):
    repair = importlib.import_module('engines.dots_tts.install').compatible_constraints
    source = tmp_path / 'recommended.txt'
    source.write_text('gradio==6.18.0\ntransformers>=4.57.2\n')
    assert repair(source) == source
    assert not (tmp_path / '.voicestudio-compatible.txt').exists()


def test_managed_install_uses_repaired_pins_and_reports_native_prerequisites(monkeypatch, tmp_path):
    si = importlib.import_module('services.sidecar_install')
    monkeypatch.setattr(si, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(si, '_locate_uv', lambda: 'uv')
    spec = si.get_spec('dots-tts')
    checkout = si.managed_checkout(spec)
    source = checkout / 'constraints' / 'recommended.txt'
    source.parent.mkdir(parents=True)
    source.write_text('gradio==6.17.0\ntransformers==4.57.0\n')
    calls = []
    def failed_install(job, argv, **kwargs):
        calls.append(argv)
        return 1
    monkeypatch.setattr(si, '_run_logged', failed_install)
    with pytest.raises(si._StepError) as error:
        si._step_install_deps(spec, si._new_job('dots-tts'))
    argument = calls[0][calls[0].index('-c') + 1]
    assert argument == source.with_name('.voicestudio-compatible.txt').as_uri()
    assert 'OpenFst' in error.value.remediation
    assert source.read_text(encoding="utf-8") == 'gradio==6.17.0\ntransformers==4.57.0\n'
