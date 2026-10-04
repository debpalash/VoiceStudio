#!/usr/bin/env python3
"""Post-install setup for platform-specific runtime dependencies.

1. **Windows: VC++ Redistributable** — PyTorch's native DLLs (c10.dll,
   torch_cpu.dll, etc.) link against vcruntime140.dll and msvcp140.dll from
   the Microsoft Visual C++ 2015-2022 Redistributable. Fresh Windows installs
   (especially debloated/LTSC-style) don't ship it. We detect and auto-install
   it silently before any `import torch` can fail.

2. **CUDA: cuDNN 8 compat** — Ensures cuDNN 8 libraries are available for
   CTranslate2 (faster-whisper / WhisperX) alongside PyTorch 2.8+'s cuDNN 9.

3. **AMD ROCm source bootstrap (opt-in)** — with `OMNIVOICE_TORCH_VARIANT=rocm`
   set, replace the lockfile's CUDA torch build with ROCm after `uv sync`
   (#1665). Linux keeps its existing torch 2.8 recipe. Native Windows uses
   x64 Python 3.12, AMD torch 2.9.1+rocm7.2.1 and CTranslate2 4.8.2 HIP wheels.
   This does not change Electron setup or guarantee application GPU support.

Default setup runs as part of `bun run setup:api`. For Windows ROCm source
installs, use `uv sync --frozen --no-dev --python 3.12` followed by
`uv run --no-sync --python 3.12 python scripts/setup.py` with the opt-in set.
See docs/install/windows-rocm-source.md for prerequisites and limitations.

Cross-platform:
  - Linux:   cuDNN 8 compat (.so.8 libs)
  - Windows: VC++ Redistributable + cuDNN 8 compat (.dll libs)
  - macOS:   skipped (no CUDA)
"""
import glob
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile

# On Windows, when this script's stdout is a *pipe* (redirected, captured by a
# parent process such as `bun run setup:api`, or CI) rather than an interactive
# console, Python defaults to the locale codepage (cp1252), which can't encode
# the ✓/⚙ status glyphs printed below — the script then dies with
# UnicodeEncodeError *before finishing setup*, taking `bun desktop` down with it.
# It only "works" in an interactive terminal by luck of the console's encoding.
# Force UTF-8 on our own streams so output is identical whether run interactively
# or piped. No-op where the streams already speak UTF-8 (macOS/Linux, modern
# Windows Terminal) or can't be reconfigured.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# ── AMD ROCm torch (opt-in) ────────────────────────────────────────────────

# Keep in sync with Electron's runtime-project.ts and
# [tool.uv.constraint-dependencies].
ROCM_TORCH_INDEX = "https://download.pytorch.org/whl/rocm6.4"
ROCM_TORCH_PINS = ("torch==2.8.0", "torchaudio==2.8.0", "torchvision==0.23.0")
WINDOWS_ROCM_RECIPE = json.loads(
    Path(__file__).with_name("windows-rocm-recipe.json").read_text(encoding="utf-8")
)
WINDOWS_ROCM_PYTHON_VERSION = WINDOWS_ROCM_RECIPE["python"]["version"]
WINDOWS_ROCM_FIND_LINKS = WINDOWS_ROCM_RECIPE["torch"]["find_links"]
WINDOWS_ROCM_TORCH_PINS = tuple(WINDOWS_ROCM_RECIPE["torch"]["packages"])
WINDOWS_ROCM_CT2_VERSION = WINDOWS_ROCM_RECIPE["ctranslate2"]["version"]
WINDOWS_ROCM_CT2_URL = WINDOWS_ROCM_RECIPE["ctranslate2"]["archive_url"]
WINDOWS_ROCM_CT2_ARCHIVE_SHA256 = WINDOWS_ROCM_RECIPE["ctranslate2"]["archive_sha256"].upper()
WINDOWS_ROCM_CT2_WHEEL_MEMBER = WINDOWS_ROCM_RECIPE["ctranslate2"]["wheel_member"]
WINDOWS_ROCM_CT2_WHEEL_SHA256 = WINDOWS_ROCM_RECIPE["ctranslate2"]["wheel_sha256"].upper()
WINDOWS_ROCM_CT2_REQUIRED_DLLS = tuple(WINDOWS_ROCM_RECIPE["ctranslate2"]["required_dlls"])


def _rocm_opt_in(environ=os.environ, platform=sys.platform):
    """Return the platform's ROCm wheel source only on explicit opt-in."""
    if environ.get("OMNIVOICE_TORCH_VARIANT", "").strip().lower() != "rocm":
        return None
    if platform == "win32":
        return environ.get("OMNIVOICE_WINDOWS_ROCM_FIND_LINKS") or WINDOWS_ROCM_FIND_LINKS
    return environ.get("OMNIVOICE_TORCH_INDEX") or ROCM_TORCH_INDEX


def _rocm_python_supported(platform=sys.platform, version=sys.version_info):
    """Enforce the recipe's Windows interpreter without restricting other platforms."""
    return platform != "win32" or (
        tuple(version[:2]) == tuple(int(part) for part in WINDOWS_ROCM_PYTHON_VERSION.split("."))
        and sysconfig.get_platform() == WINDOWS_ROCM_RECIPE["python"]["platform"]
    )


def _installed_torch_is_rocm(platform=sys.platform):
    if platform == "win32":
        pinned_versions = {
            package.partition("[")[0]: version
            for package, _, version in (pin.partition("==") for pin in WINDOWS_ROCM_TORCH_PINS)
        }
        probe = (
            "import importlib.metadata as metadata\n"
            "import torch\n"
            f"expected = {pinned_versions!r}\n"
            "print(bool(torch.version.hip) and all(\n"
            "    metadata.version(package) == version for package, version in expected.items()\n"
            "))\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0 and result.stdout.strip() == "True"
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        import torch  # noqa: WPS433 — deliberately lazy; torch is heavy
    except Exception:
        return False
    return bool(getattr(torch.version, "hip", None))


def _check_windows_rocm():
    """Raise if an isolated HIP probe cannot verify a synchronized GPU matmul."""
    subprocess.check_call(
        [
            sys.executable, "-c",
            "import torch\n"
            "if not torch.version.hip:\n"
            "    raise RuntimeError('Windows ROCm PyTorch has no HIP runtime')\n"
            "if not torch.cuda.is_available():\n"
            "    raise RuntimeError('Windows ROCm PyTorch cannot access a GPU')\n"
            "tensor = torch.ones((4, 4), device='cuda')\n"
            "result = tensor @ tensor\n"
            "torch.cuda.synchronize()\n"
            "if result[0, 0].item() != 4:\n"
            "    raise RuntimeError('Windows ROCm GPU matrix result is incorrect')\n",
        ],
        timeout=90,
    )


def rocm_torch_reinstall_cmd(index_url, python=None, platform=sys.platform):
    """`uv pip install` argv targeting THIS venv (not whatever uv guesses)."""
    if platform == "win32":
        return [
            "uv", "--no-config", "pip", "install",
            "--python", python or sys.executable,
            *WINDOWS_ROCM_TORCH_PINS,
            "--reinstall-package", "torch",
            "--reinstall-package", "torchaudio",
            "--reinstall-package", "torchvision",
            "--find-links", index_url,
        ]
    return [
        "uv", "pip", "install", "--reinstall",
        "--python", python or sys.executable,
        *ROCM_TORCH_PINS,
        "--index-url", index_url,
    ]


def _ensure_rocm_torch():
    index_url = _rocm_opt_in()
    if index_url is None:
        return
    if not _rocm_python_supported():
        raise RuntimeError(
            f"Windows ROCm requires x64 Python {WINDOWS_ROCM_PYTHON_VERSION}; "
            f"run uv sync --python {WINDOWS_ROCM_PYTHON_VERSION} "
            "with a Windows x64 interpreter first"
        )
    if _installed_torch_is_rocm():
        if sys.platform == "win32":
            _check_windows_rocm()
        print("✓ ROCm torch already installed")
        return
    print(f"⚙ OMNIVOICE_TORCH_VARIANT=rocm — swapping torch to the ROCm wheel ({index_url})")
    try:
        subprocess.check_call(rocm_torch_reinstall_cmd(index_url))
    except (OSError, subprocess.CalledProcessError) as exc:
        if sys.platform == "win32":
            raise RuntimeError("Windows ROCm PyTorch installation failed") from exc
        print(f"⚠ ROCm torch install failed ({exc}); keeping the default torch build")
        return
    if sys.platform == "win32":
        _check_windows_rocm()
    print("✓ ROCm torch installed")


# ── Windows: VC++ Redistributable ─────────────────────────────────────────

def _save_and_hash(source, destination):
    """Stream to destination in 1-MiB chunks and return the uppercase SHA-256."""
    digest = hashlib.sha256()
    with open(destination, "wb") as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    return digest.hexdigest().upper()


def _install_windows_rocm_ctranslate2():
    """Download and verify the pinned HIP wheel, then install into this interpreter.

    Check the archive and exact wheel member hashes before installation. Clean
    temporary files on failure, but do not roll back partial package changes.
    """
    with tempfile.TemporaryDirectory(prefix="voicestudio-rocm-ct2-") as temp_dir:
        archive_path = os.path.join(temp_dir, "rocm-python-wheels-Windows.zip")
        wheel_path = os.path.join(temp_dir, WINDOWS_ROCM_CT2_WHEEL_MEMBER.rsplit("/", 1)[-1])
        try:
            with urllib.request.urlopen(WINDOWS_ROCM_CT2_URL, timeout=60) as response:
                archive_hash = _save_and_hash(response, archive_path)
        except OSError as exc:
            raise RuntimeError(f"Windows ROCm CTranslate2 archive download failed: {exc}") from exc
        if archive_hash != WINDOWS_ROCM_CT2_ARCHIVE_SHA256:
            raise RuntimeError("Windows ROCm CTranslate2 archive SHA256 mismatch; refusing to extract or install")

        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = [member for member in archive.infolist() if member.filename == WINDOWS_ROCM_CT2_WHEEL_MEMBER]
                if len(members) != 1 or members[0].is_dir():
                    raise RuntimeError("Windows ROCm CTranslate2 archive must contain exactly one expected wheel")
                with archive.open(members[0]) as wheel:
                    wheel_hash = _save_and_hash(wheel, wheel_path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise RuntimeError(f"Windows ROCm CTranslate2 archive extraction failed: {exc}") from exc
        if wheel_hash != WINDOWS_ROCM_CT2_WHEEL_SHA256:
            raise RuntimeError("Windows ROCm CTranslate2 wheel SHA256 mismatch; refusing to install")

        try:
            subprocess.check_call([
                "uv", "--no-config", "pip", "install", "--no-deps", "--python",
                sys.executable, "--reinstall-package", "ctranslate2", wheel_path,
            ])
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Windows ROCm CTranslate2 wheel installation failed: {exc}") from exc


def _probe_windows_rocm_ctranslate2():
    """Return an isolated CT2 GPU probe result with SDK DLL handles kept alive.

    A nonzero exit indicates failed validation; process launch errors and
    timeouts raise RuntimeError rather than masquerading as a probe result.
    """
    probe = (
        "from contextlib import ExitStack\n"
        "from importlib.util import find_spec\n"
        "import os\n"
        "from pathlib import Path\n"
        "spec = find_spec('ctranslate2')\n"
        "if spec is None or not spec.submodule_search_locations:\n"
        "    raise RuntimeError('CTranslate2 package is not installed')\n"
        "package_dir = Path(next(iter(spec.submodule_search_locations)))\n"
        "directories = [package_dir, package_dir.parent / '_rocm_sdk_core' / 'bin',\n"
        "               package_dir.parent / '_rocm_sdk_libraries_custom' / 'bin']\n"
        "torch_spec = find_spec('torch')\n"
        "if torch_spec is not None and torch_spec.submodule_search_locations:\n"
        "    directories.append(Path(next(iter(torch_spec.submodule_search_locations))) / 'lib')\n"
        "with ExitStack() as stack:\n"
        "    for directory in dict.fromkeys(path.resolve() for path in directories):\n"
        "        if directory.is_dir():\n"
        "            stack.enter_context(os.add_dll_directory(str(directory)))\n"
        "    import ctranslate2\n"
        f"    if ctranslate2.__version__ != {WINDOWS_ROCM_CT2_VERSION!r}:\n"
        f"        raise RuntimeError(f'Expected CTranslate2 {WINDOWS_ROCM_CT2_VERSION}, found {{ctranslate2.__version__}}')\n"
        "    binary = Path(ctranslate2.__file__).with_name('ctranslate2.dll').read_bytes()\n"
        f"    required_dlls = {WINDOWS_ROCM_CT2_REQUIRED_DLLS!r}\n"
        "    if not all(name.encode('ascii') + b'\\x00' in binary for name in required_dlls):\n"
        "        raise RuntimeError('Installed CTranslate2 does not contain the Windows ROCm backend')\n"
        "    device_count = ctranslate2.get_cuda_device_count()\n"
        "    if device_count < 1:\n"
        "        raise RuntimeError('No ROCm GPU detected by CTranslate2')\n"
        "    compute_types = ctranslate2.get_supported_compute_types('cuda')\n"
        "    if 'float16' not in compute_types:\n"
        "        raise RuntimeError(f'ROCm GPU lacks float16 support: {compute_types}')\n"
    )
    try:
        return subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Windows ROCm CTranslate2 GPU validation could not run: {exc}") from exc


def _check_windows_rocm_ctranslate2():
    """Raise with bounded probe diagnostics when CT2 GPU validation fails."""
    result = _probe_windows_rocm_ctranslate2()
    if result.returncode != 0:
        diagnosis = (result.stderr or result.stdout or "no error output").strip()[-2000:]
        raise RuntimeError(
            f"Windows ROCm CTranslate2 {WINDOWS_ROCM_CT2_VERSION} cannot use GPU float16 "
            f"(exit {result.returncode}): {diagnosis}. "
            f"Check the recipe's ROCm driver/runtime, compatible AMD GPU, and Python {WINDOWS_ROCM_PYTHON_VERSION}."
        )


def _ensure_windows_rocm_ctranslate2():
    """Reuse validated CT2 or install the pinned HIP wheel and recheck the GPU."""
    if _probe_windows_rocm_ctranslate2().returncode == 0:
        print("Windows ROCm CTranslate2 GPU float16 already ready")
        return
    print(f"Installing native Windows ROCm CTranslate2 {WINDOWS_ROCM_CT2_VERSION}...")
    _install_windows_rocm_ctranslate2()
    _check_windows_rocm_ctranslate2()
    print("Windows ROCm CTranslate2 GPU float16 ready")


def _ensure_vcredist_windows():
    """Check for and install the VC++ 2015-2022 Redistributable on Windows.

    PyTorch's native libraries (c10.dll, torch_cpu.dll, etc.) are built with
    MSVC and dynamically link against vcruntime140.dll + msvcp140.dll.  These
    ship with Visual Studio / Build Tools but are NOT part of Windows itself.
    On a fresh or debloated install the very first `import torch` crashes with:

        OSError: [WinError 126] The specified module could not be found.
        Error loading ...\\torch\\lib\\c10.dll or one of its dependencies.

    This function silently downloads and installs the official x64 redist
    package from Microsoft if the runtime DLLs are missing.
    """
    if sys.platform != "win32":
        return

    # Check if vcruntime140.dll is already loadable
    import ctypes
    try:
        ctypes.WinDLL("vcruntime140.dll")
        print("✓ VC++ Redistributable: already installed")
        return
    except OSError:
        pass

    print("⚙ VC++ Redistributable not found — installing (required for PyTorch)...")

    import tempfile
    import urllib.request

    vc_url = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    installer = os.path.join(tempfile.gettempdir(), "vc_redist.x64.exe")

    try:
        # Download
        print("  Downloading VC++ Redistributable...")
        urllib.request.urlretrieve(vc_url, installer)

        # Silent install (/install /quiet /norestart)
        print("  Installing silently...")
        result = subprocess.run(
            [installer, "/install", "/quiet", "/norestart"],
            timeout=120,
            capture_output=True,
        )

        # Verify it worked
        try:
            ctypes.WinDLL("vcruntime140.dll")
            print("✓ VC++ Redistributable: installed successfully")
        except OSError:
            # Exit code 3010 = success but reboot required
            if result.returncode == 3010:
                print("✓ VC++ Redistributable: installed (reboot recommended)")
            else:
                print(f"⚠ VC++ Redistributable: install may have failed (exit code {result.returncode})")
                print("  Manual install: https://aka.ms/vs/17/release/vc_redist.x64.exe")
    except Exception as e:
        print(f"⚠ VC++ Redistributable: auto-install failed: {e}")
        print("  Manual install: https://aka.ms/vs/17/release/vc_redist.x64.exe")
    finally:
        # Clean up installer
        try:
            os.remove(installer)
        except OSError:
            pass


# ── cuDNN 8 compat ────────────────────────────────────────────────────────

def _find_compat_dir():
    """Return the cudnn8_compat target directory, auto-detecting venv layout."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    venv_dir = os.path.join(project_root, ".venv")

    if not os.path.isdir(venv_dir):
        return None

    if sys.platform == "win32":
        # Windows: .venv/Lib/site-packages/
        sp = os.path.join(venv_dir, "Lib", "site-packages", "cudnn8_compat")
    else:
        # Linux: .venv/lib/pythonX.Y/site-packages/
        pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        sp = os.path.join(venv_dir, "lib", pyver, "site-packages", "cudnn8_compat")

    return sp


def _cudnn8_lib_dir(compat_dir):
    """Return the cuDNN lib subdirectory within the compat install."""
    if sys.platform == "win32":
        return os.path.join(compat_dir, "nvidia", "cudnn", "bin")
    return os.path.join(compat_dir, "nvidia", "cudnn", "lib")


def _count_cudnn8_libs(lib_dir):
    """Count cuDNN 8 shared libraries in the given directory."""
    if sys.platform == "win32":
        return len(glob.glob(os.path.join(lib_dir, "cudnn*64_8.dll")))
    return len(glob.glob(os.path.join(lib_dir, "libcudnn*.so.8")))


def main():
    # ── Step 1: Windows VC++ Redistributable ──────────────────────────────
    _ensure_vcredist_windows()

    # macOS — no CUDA, nothing to do
    if sys.platform == "darwin":
        return

    # ── Step 2: opt-in AMD ROCm torch ────────────────────────────────────
    if sys.platform.startswith("linux") or sys.platform == "win32":
        _ensure_rocm_torch()

    if sys.platform == "win32" and _rocm_opt_in() is not None:
        _ensure_windows_rocm_ctranslate2()
        return

    compat_dir = _find_compat_dir()
    if compat_dir is None:
        return

    lib_dir = _cudnn8_lib_dir(compat_dir)

    # Already installed?
    if os.path.isdir(lib_dir):
        n = _count_cudnn8_libs(lib_dir)
        if n >= 5:
            print(f"✓ cuDNN 8 compat: {n} libraries ready")
            return

    # Check if CUDA is available before installing GPU-only libs
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import torch; print(torch.cuda.is_available())"],
            capture_output=True, text=True, timeout=30,
        )
        if result.stdout.strip() != "True":
            print("✓ No CUDA — cuDNN 8 compat not needed")
            return
    except Exception:
        pass  # Can't detect CUDA — install anyway, it's harmless on CPU

    print("⚙ Installing cuDNN 8 compatibility libraries for CTranslate2...")
    try:
        # `uv venv` doesn't seed pip into the venv, so `sys.executable -m pip`
        # fails with "No module named pip". `uv pip install --python` talks to
        # the interpreter directly without needing pip installed inside it.
        subprocess.run(
            [
                "uv", "pip", "install",
                "--no-deps", "--target", compat_dir,
                "--python", sys.executable,
                "nvidia-cudnn-cu12==8.9.7.29",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        n = _count_cudnn8_libs(lib_dir)
        print(f"✓ cuDNN 8 installed: {n} libraries")
    except subprocess.CalledProcessError as e:
        print(f"⚠ cuDNN 8 install failed (transcription may not work on CUDA):")
        print(f"  {(e.stderr or '')[:300]}")
    except Exception as e:
        print(f"⚠ cuDNN 8 install skipped: {e}")


if __name__ == "__main__":
    main()
