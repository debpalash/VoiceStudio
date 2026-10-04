"""The artifact rehearsal must exercise installation, not just the setup screen."""
from pathlib import Path

import yaml


def test_rehearsal_installs_runtime_in_a_separate_packaged_launch():
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / '.github/workflows/electron-build.yml').read_text(encoding='utf-8')
    )
    steps = workflow['jobs']['package']['steps']
    runtime = [step for step in steps if '--install' in step.get('run', '')]
    assert len(runtime) == 1, 'Rehearsal must actually install and start the packaged runtime'
    assert runtime[0]['if'] == 'matrix.local_runtime'
    targets = workflow['jobs']['package']['strategy']['matrix']['include']
    assert {(t['platform'], t['arch']) for t in targets if t['local_runtime']} == {
        ('linux', 'x64'), ('win32', 'x64'), ('win32', 'arm64'), ('darwin', 'arm64'),
    }
    assert {(t['platform'], t['arch']) for t in targets if not t['local_runtime']} == {
        ('darwin', 'x64'),
    }
    assert '--setup' not in runtime[0]['run'], '--setup would bypass runtime installation'
    assert 'xvfb-run -a node tests/packaged-smoke.mjs --install' in runtime[0]['run']
    assert 'node tests/packaged-smoke.mjs --setup' in '\n'.join(s.get('run', '') for s in steps)
    assert workflow['permissions']['contents'] == 'read'
    assert '--publish never' in '\n'.join(s.get('run', '') for s in steps)


def test_windows_on_arm_leg_builds_natively_and_installs_the_runtime():
    """Snapdragon PCs: native ARM64 shell + helper, x64 Python runtime install."""
    job = yaml.safe_load(
        (Path(__file__).parents[1] / '.github/workflows/electron-build.yml').read_text()
    )['jobs']['package']
    leg = next(t for t in job['strategy']['matrix']['include']
               if (t['platform'], t['arch']) == ('win32', 'arm64'))
    assert leg['runner'] == 'windows-11-arm'
    assert leg['target'] == 'aarch64-pc-windows-msvc'
    assert '--arm64' in leg['flags'] and '--win' in leg['flags']
    assert leg['local_runtime'] is True
    # A brand-new target must report, not hide, the established legs.
    assert leg['experimental'] is True and job['continue-on-error']


def test_install_smoke_uses_an_offline_fresh_model_cache():
    """Fresh installation must not reuse models or trigger model downloads."""
    smoke = (Path(__file__).parents[1] / 'electron/tests/packaged-smoke.mjs').read_text(encoding='utf-8')
    assert "mkdtempSync(join(tmpdir(), 'voicestudio-packaged-check-'))" in smoke
    assert "if (setup || install || existing)" in smoke
    assert "HF_HUB_CACHE: join(profile, 'hf-home', 'hub')" in smoke
    assert "HF_HUB_OFFLINE: firstSound || cosyvoiceInstallSmoke ? '0' : '1'" in smoke
    assert "TRANSFORMERS_OFFLINE: firstSound || cosyvoiceInstallSmoke ? '0' : '1'" in smoke
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / '.github/workflows/electron-build.yml').read_text(encoding='utf-8')
    )
    for step in workflow['jobs']['package']['steps']:
        if '--install' in step.get('run', ''):
            assert '--first-sound' not in step['run']
            assert '--cosyvoice-install-smoke' not in step['run']
            assert 'VOICESTUDIO_TEST_PROFILE' not in step.get('env', {})
