"""Broken Torch companion DLLs must invalidate readiness and enter repair."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_FIXTURE = r"""
import builtins
import json
import sys
from types import SimpleNamespace
from backend.core.windows_rocm import probe

recipe = probe.load_recipe()
versions = probe.expected_versions(recipe)
probe.validate_target = lambda: None
probe.metadata.version = versions.__getitem__
probe.package_root = lambda name: __import__('pathlib').Path('unused')
probe.sha256 = lambda path: recipe['ctranslate2']['binary_sha256']
probe.initialize_dll_search = lambda: None
probe.sysconfig.get_platform = lambda: 'win-amd64'

class Tensor:
    device = SimpleNamespace(type='cuda')
    def __matmul__(self, other): return self
    def __eq__(self, other): return self
    def __getitem__(self, index): return self
    def item(self): return 4.0

sys.modules['torch'] = SimpleNamespace(
    version=SimpleNamespace(hip='7.2.53211'),
    cuda=SimpleNamespace(is_available=lambda: True, synchronize=lambda: None,
        get_device_properties=lambda index: SimpleNamespace(gcnArchName='gfx1100')),
    ones=lambda *a, **kw: Tensor(), all=lambda tensor: tensor)
sys.modules['ctranslate2'] = SimpleNamespace(
    get_cuda_device_count=lambda: 1, get_supported_compute_types=lambda device: {'float16'})
sys.modules['torchaudio'] = SimpleNamespace()
sys.modules['torchvision'] = SimpleNamespace()
real_import = builtins.__import__
broken_package = sys.argv[1]
def distribution(name):
    files = [] if broken_package.startswith('prerequisite') else [probe.metadata.PackagePath(name + '/broken.pyd')]
    return SimpleNamespace(files=files, locate_file=lambda file: __import__('pathlib').Path('__missing_fixture__'))
probe.metadata.distribution = distribution
if broken_package == 'device':
    sys.modules['torch'].ones = lambda *a, **k: (_ for _ in ()).throw(OSError('native device failure'))
if broken_package == 'no_gpu':
    sys.modules['torch'].cuda.is_available = lambda: False
if broken_package == 'unsupported':
    sys.modules['torch'].cuda.get_device_properties = lambda index: SimpleNamespace(gcnArchName='gfx1031')
if broken_package == 'missing':
    probe.metadata.version = lambda name: (_ for _ in ()).throw(probe.metadata.PackageNotFoundError(name))
if broken_package == 'version':
    probe.metadata.version = lambda name: 'wrong-version'
if broken_package == 'integrity':
    probe.sha256 = lambda path: 'damaged'
if broken_package == 'ct2_device':
    sys.modules['ctranslate2'].get_cuda_device_count = lambda: (_ for _ in ()).throw(RuntimeError('driver'))
def native_import(name, *args, **kwargs):
    if broken_package.startswith('prerequisite') and name == 'torchaudio':
        error = RuntimeError if broken_package.endswith('runtime') else OSError
        raise error('external prerequisite')
    if name == broken_package:
        raise OSError('native companion DLL failed to load: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = native_import
try:
    print(json.dumps(probe.probe()))
except Exception as error:
    print(json.dumps({"ready": False, "scope": "model-free", "category": getattr(error, "category", "unknown"), "error": str(error)}))
    print(str(error), file=sys.stderr)
    raise SystemExit(1)
"""


@pytest.mark.parametrize("broken_package", ["torch", "torchaudio", "torchvision", "ctranslate2"])
def test_companion_native_import_failure_rejects_ready_and_starts_repair(monkeypatch, broken_package):
    # Real subprocess exercises production probe() while only native/host APIs are faked.
    result = subprocess.run(
        [sys.executable, "-c", PROBE_FIXTURE, broken_package],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["category"] == "native_import"
    assert broken_package in result.stderr
    assert '"ready": true' not in result.stdout

    installer = importlib.import_module("core.windows_rocm.install")
    monkeypatch.setattr(installer, "validate_target", lambda: None)
    # Preserve real verifier behavior at the failed-child boundary.
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: result)

    def repair_started(*args):
        raise RuntimeError("verified artifact download entered for repair")

    monkeypatch.setattr(installer, "download_archive", repair_started)
    with pytest.raises(RuntimeError, match="download entered for repair"):
        installer.provision("unused-uv")


def test_healthy_native_fixture_reaches_readiness():
    result = subprocess.run(
        [sys.executable, "-c", PROBE_FIXTURE, "none"],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"ready": true' in result.stdout


@pytest.mark.parametrize(
    "mode,category",
    [
        ("prerequisite_os", "native_runtime"),
        ("prerequisite_runtime", "native_runtime"),
        ("device", "device_runtime"),
        ("ct2_device", "device_runtime"),
        ("no_gpu", "device_runtime"),
        ("unsupported", "unsupported_gpu"),
        ("missing", "package_missing"),
        ("version", "package_version"),
        ("integrity", "binary_integrity"),
    ],
)
def test_probe_classifies_by_evidence_and_execution_stage(mode, category):
    result = subprocess.run(
        [sys.executable, "-c", PROBE_FIXTURE, mode],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["category"] == category
