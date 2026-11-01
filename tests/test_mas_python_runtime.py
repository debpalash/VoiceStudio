"""Private MAS staging must produce a relocatable, auditable Python tree."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_stager():
    spec = importlib.util.spec_from_file_location("mas_python_runtime", ROOT / "scripts/stage_mas_python.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_runtime(tmp_path, symlink_or_skip):
    base = tmp_path / "base"
    packages = tmp_path / "environment/lib/python3.11/site-packages"
    (base / "bin").mkdir(parents=True)
    (base / "lib/python3.11").mkdir(parents=True)
    (base / "lib/python3.11/os.py").write_text("# stdlib\n")
    (base / "bin/python3.11").write_bytes(b"interpreter fixture")
    symlink_or_skip(base / "bin/python3", "python3.11")
    packages.mkdir(parents=True)
    (packages / "sample.py").write_text("VALUE = 42\n")
    (packages / "sample_assets").mkdir()
    (packages / "sample_assets/model.pth").write_bytes(b"\x80binary model checkpoint")
    info = packages / "sample-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: sample\nVersion: 1.0\nLicense-Expression: MIT\n")
    (info / "LICENSE").write_text("Sample license notice\n")
    return base, packages


def test_staging_preserves_relative_links_licenses_and_records_hashes(tmp_path, symlink_or_skip):
    stage = load_stager()
    base, packages = fixture_runtime(tmp_path, symlink_or_skip)
    lock = tmp_path / "uv.lock"
    lock.write_text('version = 1\n')
    target = tmp_path / "bundle/python-runtime"
    manifest = stage.stage_runtime(base, packages, target, lock, "3.11.15", "arm64")
    assert (target / "bin/python3").readlink() == Path("python3.11")
    assert (target / "lib/python3.11/site-packages/sample.py").read_text() == "VALUE = 42\n"
    assert manifest["packages"][0]["license"] == "MIT"
    assert manifest["packages"][0]["notices"]
    assert manifest["redistributionApproved"] is False
    assert len(manifest["lockSha256"]) == 64
    assert manifest["files"]["lib/python3.11/site-packages/sample.py"]["sha256"]
    assert json.loads((target / "manifest.json").read_text())["python"] == "bin/python3.11"


@pytest.mark.parametrize("escape", ["absolute-link", "relative-link", "pth"])
def test_staging_rejects_dependencies_on_paths_outside_the_bundle(tmp_path, escape, symlink_or_skip):
    stage = load_stager()
    base, packages = fixture_runtime(tmp_path, symlink_or_skip)
    outside = tmp_path / "private-source"
    outside.write_text("outside bundle\n")
    if escape == "pth":
        (packages / "editable.pth").write_text(str(outside) + "\n")
    else:
        symlink_or_skip(packages / "external", outside if escape == "absolute-link" else "../../../../private-source")
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n")
    target = tmp_path / "python-runtime"
    with pytest.raises(ValueError):
        stage.stage_runtime(base, packages, target, lock, "3.11.15", "arm64")
    assert not target.exists()


def test_phonemizer_staging_isolates_native_instances_and_records_vendor_patch(tmp_path):
    stage = load_stager()
    upstream = Path(importlib.util.find_spec("phonemizer").origin).parent / "backend/espeak/api.py"
    runtime = tmp_path / "python-runtime"
    packages = runtime / "lib/python3.11/site-packages"
    api = packages / "phonemizer/backend/espeak/api.py"
    api.parent.mkdir(parents=True)
    shutil.copy2(upstream, api)
    library = packages / "espeakng_loader/libespeak-ng.dylib"
    library.parent.mkdir()
    library.write_bytes(b"native image staged before signing")
    patches = stage.stage_phonemizer(runtime, packages)
    assert patches[0]["sourceSha256"] == stage.sha256(upstream)
    assert patches[0]["patchedSha256"] == stage.sha256(api)
    assert patches[0]["sourceSha256"] != patches[0]["patchedSha256"]
    images = list((runtime / "native/espeak").rglob("*.dylib"))
    assert len(images) == 128
    assert all(path.read_bytes() == library.read_bytes() for path in images)
    compile(api.read_text(), str(api), "exec")


def test_phonemizer_adapter_rejects_unreviewed_sdk_changes_before_staging_code(tmp_path):
    stage = load_stager()
    packages = tmp_path / "lib/python3.11/site-packages"
    api = packages / "phonemizer/backend/espeak/api.py"
    api.parent.mkdir(parents=True)
    api.write_text("# updated upstream SDK\n")
    library = packages / "espeakng_loader/libespeak-ng.dylib"
    library.parent.mkdir()
    library.write_bytes(b"native image")
    with pytest.raises(ValueError, match="pinned phonemizer source changed"):
        stage.stage_phonemizer(tmp_path, packages)
    assert not (tmp_path / "native").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Exercises the portable macOS CPython distribution")
def test_real_interpreter_runs_after_relocation_without_original_venv(tmp_path):
    stage = load_stager()
    environment = tmp_path / "build environment"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    executable = environment / "bin/python"
    inputs = stage.interpreter_inputs(executable)
    packages = Path(inputs["sitePackages"])
    (packages / "mas_relocation_probe.py").write_text("VALUE = 42\n")
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n")
    target = tmp_path / "staging/python-runtime"
    stage.stage_runtime(Path(inputs["basePrefix"]), packages, target, lock, inputs["version"], inputs["arch"])
    moved = tmp_path / "Moved App ä/Contents/Resources/python-runtime"
    moved.parent.mkdir(parents=True)
    target.rename(moved)
    shutil.rmtree(environment)
    inherited = {k: v for k, v in os.environ.items() if k not in {"PYTHONHOME", "PYTHONPATH"}}
    result = subprocess.run(
        [str(moved / "bin/python3.11"), "-I", "-B", "-c",
         "import mas_relocation_probe,ssl,sqlite3,mimetypes; "
         "assert mas_relocation_probe.VALUE == 42; "
         "assert mimetypes.knownfiles == []; "
         "assert mimetypes.guess_type('sample.html')[0] == 'text/html'"],
        env=inherited, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
