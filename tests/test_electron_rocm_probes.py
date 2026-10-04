import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import zipfile

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "electron/src/main/runtime-project.ts"
RECIPE = json.loads(
    (SOURCE.parents[3] / "scripts/windows-rocm-recipe.json").read_text(encoding="utf-8")
)
OPTIMIZATIONS = [([], "0"), (["-O"], "0"), (["-OO"], "0"), ([], "2")]
OPTIMIZATION_IDS = ["normal", "optimized", "fully-optimized", "inherited-optimization"]


def _expand_template(token: str) -> str:
    def replacement(match):
        expression = match.group(1)
        path = re.fullmatch(
            r"JSON\.stringify\(windowsRocmRecipe((?:\.[a-z_][a-z_0-9]*)+)\)", expression
        )
        assert path, f"Unsupported Electron snippet expression: {expression}"
        value = RECIPE
        for key in path.group(1).split(".")[1:]:
            value = value[key]
        assert isinstance(value, str) or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ), f"Not a Python-compatible recipe literal: {expression}"
        return json.dumps(value)

    return re.sub(r"\$\{([^{}]+)\}", replacement, token[1:-1])


@pytest.mark.parametrize("field", ["version", "required_dlls"])
def test_recipe_template_expansion_preserves_escaped_literals(monkeypatch, field):
    value = 'quoted"\\value\n${not_code}'
    expected = [value] if field == "required_dlls" else value
    monkeypatch.setitem(RECIPE["ctranslate2"], field, expected)
    rendered = _expand_template(f"`${{JSON.stringify(windowsRocmRecipe.ctranslate2.{field})}}`")
    assert ast.literal_eval(rendered) == expected


@pytest.mark.parametrize("expression", ["process.exit()", "windowsRocmRecipe", "JSON.stringify(other.version)"])
def test_recipe_template_expansion_rejects_unknown_expressions(expression):
    with pytest.raises(AssertionError, match="Unsupported Electron snippet expression"):
        _expand_template(f"`${{{expression}}}`")


def _snippet(name: str) -> str:
    source = SOURCE.read_text(encoding="utf-8")
    declaration = re.search(rf"\bconst {name}\s*=\s*(.*?);\r?\n", source, re.DOTALL)
    assert declaration, f"Missing Electron Python snippet: {name}"
    expression = declaration.group(1).strip()
    if expression.startswith("["):
        assert expression.endswith("].join('\\n')"), expression
        expression = expression[1 : expression.rindex("]")]
        tokens = re.findall(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`", expression)
        return "\n".join(
            _expand_template(token) if token.startswith("`") else ast.literal_eval(token)
            for token in tokens
        )
    if expression.startswith("`"):
        return expression[1:-1]
    return ast.literal_eval(expression)


def _run(name, bootstrap, optimization, arguments=()):
    return _run_script(textwrap.dedent(bootstrap) + "\n" + _snippet(name), optimization, arguments)


def _run_script(script, optimization, arguments=()):
    flags, inherited = optimization
    return subprocess.run(
        [sys.executable, *flags, "-c", script, *arguments],
        env={**os.environ, "PYTHONOPTIMIZE": inherited, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize(
    "expected_version,actual_version,machine,requires_x64,valid",
    [
        ((3, 11), (3, 11), "ARM64", False, True),
        ((3, 11), (3, 12), "AMD64", False, False),
        ((3, 12), (3, 11), "AMD64", False, False),
        ((3, 12), (3, 12), "AMD64", False, True),
        ((3, 11), (3, 11), "AMD64", True, True),
        ((3, 11), (3, 11), "X86_64", True, True),
        ((3, 11), (3, 11), "ARM64", True, False),
        ((3, 11), (3, 11), "unknown", True, False),
    ],
)
def test_interpreter_guard_checks_version_and_windows_arm_architecture(
    optimization, expected_version, actual_version, machine, requires_x64, valid
):
    source = SOURCE.read_text(encoding="utf-8")
    template = re.search(r"`(import sys.*?)`", source, re.DOTALL)
    assert template, "Missing interpreter version guard"
    probe = template.group(1).replace("${expectedPython}", repr(expected_version))
    probe = probe.replace(r"\n", "\n")
    if requires_x64:
        architecture = re.search(r'''(['"])(?:\\n|; )import platform.*?\1''', source)
        assert architecture, "Missing Windows ARM interpreter architecture guard"
        probe += ast.literal_eval(architecture.group(0))
    bootstrap = textwrap.dedent(
        f"""
        import sys
        from types import SimpleNamespace
        sys.version_info = {actual_version!r}
        sys.modules['platform'] = SimpleNamespace(machine=lambda: {machine!r})
        """
    )
    result = _run_script(bootstrap + "\n" + probe, optimization)
    assert (result.returncode == 0) == valid, result.stderr
    if not valid:
        assert (
            "Unsupported runtime Python version"
            if expected_version != actual_version
            else "Windows ARM requires an x64 runtime interpreter"
        ) in result.stderr


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize("scenario", ["ready", "no-hip", "unavailable", "kernel-error", "wrong-result"])
def test_torch_probe_executes_and_validates_gpu_kernel(optimization, scenario):
    bootstrap = f"""
        import sys
        from types import SimpleNamespace
        scenario = {scenario!r}

        class Tensor:
            def __matmul__(self, other):
                print('gpu-matmul')
                if scenario == 'kernel-error':
                    raise RuntimeError('GPU kernel failed')
                return self

            def __getitem__(self, index):
                return self

            def item(self):
                return 0 if scenario == 'wrong-result' else 4

        def ones(shape, device):
            if shape != (4, 4) or device != 'cuda':
                raise RuntimeError('Expected a GPU tensor')
            print('gpu-allocation')
            return Tensor()

        sys.modules['torch'] = SimpleNamespace(
            version=SimpleNamespace(hip=None if scenario == 'no-hip' else '7.2.1'),
            cuda=SimpleNamespace(is_available=lambda: scenario != 'unavailable'),
            ones=ones,
        )
    """
    result = _run("WINDOWS_ROCM_PROBE", bootstrap, optimization)
    assert (result.returncode == 0) == (scenario == "ready"), result.stderr
    if scenario in {"no-hip", "unavailable"}:
        assert "ROCm cannot access the AMD GPU" in result.stderr
        assert "gpu-allocation" not in result.stdout
    else:
        assert "gpu-matmul" in result.stdout, "The GPU kernel was optimized away"


def _ctranslate2_bootstrap(tmp_path, scenario, missing_directory=None):
    directory = tmp_path / "CT2 site-packages" / "ctranslate2"
    torch_directory = tmp_path / "separate torch installation" / "torch"
    directories = [
        directory,
        directory.parent / "_rocm_sdk_core" / "bin",
        directory.parent / "_rocm_sdk_libraries_custom" / "bin",
        torch_directory / "lib",
    ]
    present = [path for path in directories if path.parent.name != missing_directory]
    for path in present:
        path.mkdir(parents=True, exist_ok=True)
    markers = [name.encode("ascii") + b"\0" for name in RECIPE["ctranslate2"]["required_dlls"]]
    library = directory / "ctranslate2.dll"
    if scenario == "nvidia-wheel":
        library.write_bytes(b"MZ\0cublas64_12.dll\0cudart64_12.dll\0")
    elif scenario == "missing-marker":
        library.write_bytes(b"MZ\0" + b"".join(markers[:-1]))
    elif scenario == "missing-first-marker":
        library.write_bytes(b"MZ\0" + b"".join(markers[1:]))
    elif scenario == "unterminated-markers":
        library.write_bytes(b"MZ\0" + b"".join(marker[:-1] + b".invalid\0" for marker in markers))
    elif scenario == "empty-dll":
        library.write_bytes(b"")
    elif scenario != "missing-dll":
        library.write_bytes(b"MZ\0" + b"".join(markers))
    return f"""
        import builtins
        import importlib.util
        import os
        import sys
        from types import SimpleNamespace
        scenario = {scenario!r}
        expected = set({[str(path.resolve()) for path in present]!r})
        active = set()
        os.environ['PATH'] = ''
        real_find_spec = importlib.util.find_spec

        def find_spec(name):
            if name == 'ctranslate2':
                return None if scenario == 'missing-ct2' else SimpleNamespace(submodule_search_locations=[{str(directory)!r}])
            if name == 'torch':
                return None if scenario == 'missing-torch' else SimpleNamespace(submodule_search_locations=[{str(torch_directory)!r}])
            return real_find_spec(name)

        importlib.util.find_spec = find_spec

        class DllDirectory:
            def __init__(self, path):
                self.path = path
                self.closed = False
                active.add(path)
                print('dll-open')

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                self.close()

            def close(self):
                if not self.closed:
                    self.closed = True
                    active.remove(self.path)
                    print('dll-close')

            def __del__(self):
                self.close()

        def add_dll_directory(path):
            if path not in expected:
                raise RuntimeError('Unexpected DLL directory: ' + path)
            if scenario == 'registration-error' and '_rocm_sdk_libraries_custom' in path:
                raise OSError('DLL directory registration failed')
            return DllDirectory(path)

        os.add_dll_directory = add_dll_directory

        def require_open_directories():
            if active != expected:
                raise RuntimeError('Bundled DLL directory handles are missing or closed')

        def device_count():
            require_open_directories()
            print('ct2-device-count')
            return 0 if scenario == 'unavailable' else 1

        def compute_types(device, index):
            require_open_directories()
            if device != 'cuda' or index != 0:
                raise RuntimeError('Expected the first HIP device')
            print('ct2-compute-types')
            return {{'int8'}} if scenario == 'missing-float16' else {{'float16'}}

        native = SimpleNamespace(
            __version__='0.0.0' if scenario == 'wrong-version' else {RECIPE['ctranslate2']['version']!r},
            get_cuda_device_count=device_count,
            get_supported_compute_types=compute_types,
        )
        real_import = builtins.__import__

        def native_import(name, *arguments, **keywords):
            if name == 'ctranslate2':
                require_open_directories()
                print('ct2-import')
                if scenario == 'import-error':
                    raise ImportError('Native CTranslate2 import failed')
                return native
            if name == 'torch':
                raise RuntimeError('Torch must not be imported to discover DLL paths')
            return real_import(name, *arguments, **keywords)

        builtins.__import__ = native_import
    """


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize(
    "scenario,message",
    [
        ("nvidia-wheel", "CTranslate2 lacks the required HIP DLL markers"),
        ("wrong-version", "Unsupported CTranslate2 ROCm version"),
        ("missing-marker", "CTranslate2 lacks the required HIP DLL markers"),
        ("missing-first-marker", "CTranslate2 lacks the required HIP DLL markers"),
        ("unterminated-markers", "CTranslate2 lacks the required HIP DLL markers"),
        ("missing-dll", "CTranslate2 has no native Windows DLL"),
        ("empty-dll", "CTranslate2 lacks the required HIP DLL markers"),
    ],
)
def test_ctranslate2_probe_rejects_non_recipe_wheels_before_device_checks(
    tmp_path, optimization, scenario, message
):
    bootstrap = _ctranslate2_bootstrap(tmp_path, scenario)
    result = _run("WINDOWS_ROCM_CT2_PROBE", bootstrap, optimization)
    assert result.returncode != 0, "A CUDA device and float16 do not prove a HIP wheel"
    assert message in result.stderr
    assert "ct2-device-count" not in result.stdout
    assert "ct2-compute-types" not in result.stdout
    assert result.stdout.count("dll-open") == result.stdout.count("dll-close")
    if scenario == "wrong-version":
        assert "ct2-import" in result.stdout
    else:
        assert "ct2-import" not in result.stdout


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize("scenario", ["ready", "unavailable", "missing-float16"])
def test_ctranslate2_probe_checks_device_and_compute_support(tmp_path, optimization, scenario):
    bootstrap = _ctranslate2_bootstrap(tmp_path, scenario)
    result = _run("WINDOWS_ROCM_CT2_PROBE", bootstrap, optimization)
    assert (result.returncode == 0) == (scenario == "ready"), result.stderr
    assert result.stdout.count("dll-open") == 4
    assert result.stdout.count("dll-close") == 4
    assert result.stdout.index("ct2-import") > result.stdout.rindex("dll-open")
    assert "ct2-device-count" in result.stdout
    if scenario != "unavailable":
        assert "ct2-compute-types" in result.stdout
        assert result.stdout.index("dll-close") > result.stdout.index("ct2-compute-types")


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize("missing_directory", ["_rocm_sdk_core", "_rocm_sdk_libraries_custom"])
def test_ctranslate2_probe_skips_absent_optional_dll_directories(
    tmp_path, optimization, missing_directory
):
    bootstrap = _ctranslate2_bootstrap(tmp_path, "ready", missing_directory)
    result = _run("WINDOWS_ROCM_CT2_PROBE", bootstrap, optimization)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("dll-open") == 3
    assert result.stdout.count("dll-close") == 3


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize("scenario", ["registration-error", "import-error"])
def test_ctranslate2_probe_closes_dll_handles_on_failure(tmp_path, optimization, scenario):
    bootstrap = _ctranslate2_bootstrap(tmp_path, scenario)
    result = _run("WINDOWS_ROCM_CT2_PROBE", bootstrap, optimization)
    assert result.returncode != 0
    assert result.stdout.count("dll-open") == (2 if scenario == "registration-error" else 4)
    assert result.stdout.count("dll-close") == result.stdout.count("dll-open")
    assert (
        "DLL directory registration failed"
        if scenario == "registration-error"
        else "Native CTranslate2 import failed"
    ) in result.stderr
    assert "ct2-device-count" not in result.stdout


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize("scenario", ["missing-ct2", "missing-torch"])
def test_ctranslate2_probe_rejects_missing_packages_before_native_import(
    tmp_path, optimization, scenario
):
    bootstrap = _ctranslate2_bootstrap(tmp_path, scenario)
    result = _run("WINDOWS_ROCM_CT2_PROBE", bootstrap, optimization)
    assert result.returncode != 0
    assert "package is missing" in result.stderr
    assert "ct2-import" not in result.stdout


@pytest.mark.parametrize("optimization", OPTIMIZATIONS, ids=OPTIMIZATION_IDS)
@pytest.mark.parametrize("scenario", ["ready", "bad-checksum", "missing-member", "duplicate-member"])
def test_wheel_extraction_verifies_members_and_checksum(tmp_path, optimization, scenario):
    archive = tmp_path / "rocm.zip"
    destination = tmp_path / "wheel.incoming"
    payload = b"verified ROCm wheel"
    member = "temp-windows/ctranslate2.whl"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("other.whl" if scenario == "missing-member" else member, payload)
        if scenario == "duplicate-member":
            with pytest.warns(UserWarning, match="Duplicate name"):
                package.writestr(member, payload)
    destination.write_bytes(b"previous file")
    checksum = "0" * 64 if scenario == "bad-checksum" else hashlib.sha256(payload).hexdigest()
    result = _run(
        "WINDOWS_ROCM_CT2_EXTRACT",
        "",
        optimization,
        (str(archive), member, checksum, str(destination)),
    )
    assert (result.returncode == 0) == (scenario == "ready"), result.stderr
    if scenario == "ready":
        assert destination.read_bytes() == payload
    else:
        assert destination.read_bytes() == b"previous file"
        assert (
            "Invalid ROCm wheel checksum"
            if scenario == "bad-checksum"
            else "Expected ROCm wheel missing or duplicated"
        ) in result.stderr
