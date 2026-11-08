#!/usr/bin/env python3
"""Post-install setup for platform-specific runtime dependencies.

1. **Windows: VC++ Redistributable** — PyTorch's native DLLs (c10.dll,
   torch_cpu.dll, etc.) link against vcruntime140.dll and msvcp140.dll from
   the Microsoft Visual C++ 2015-2022 Redistributable. Fresh Windows installs
   (especially debloated/LTSC-style) don't ship it. We detect and auto-install
   it silently before any `import torch` can fail. Opted-in Windows ROCm
   setup checks locally and requires manual installation instead.

2. **CUDA: cuDNN 8 compat** — Ensures cuDNN 8 libraries are available for
   CTranslate2 (faster-whisper / WhisperX) alongside PyTorch 2.10+'s cuDNN 9.

3. **ROCm torch for AMD GPUs (opt-in)** — setting `OMNIVOICE_TORCH_VARIANT=rocm`
   makes this script exchange the lockfile's CUDA build of torch for ROCm
   wheels after `uv sync` (#1665). The installed desktop app does the same
   exchange, which keeps `bun run desktop` on AMD hardware off the CPU.

Run automatically as part of `bun run setup:api`. Windows ROCm setup requires
manual VC++ installation first when that prerequisite is missing.

Cross-platform:
  - Linux:   cuDNN 8 compat (.so.8 libs)
  - Windows: VC++ Redistributable + cuDNN 8 compat (.dll libs)
  - macOS:   skipped (no CUDA)
"""
import os
import re
import sys
import subprocess
import glob

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


# ── Linux: AMD GPU torch build, only on request ───────────────────────────

# Keep this index and these pins in step with Electron's runtime-project.ts
# and with [tool.uv.constraint-dependencies] in pyproject.toml.
ROCM_TORCH_INDEX = "https://download.pytorch.org/whl/rocm7.1"
ROCM_TORCH_PINS = ("torch==2.10.0", "torchaudio==2.10.0", "torchvision==0.25.0")


def _rocm_opt_in(environ=os.environ):
    """Wheel index to use when ROCm was requested through the environment; otherwise None."""
    wanted = environ.get("OMNIVOICE_TORCH_VARIANT", "")
    if wanted.strip().lower() == "rocm":
        return environ.get("OMNIVOICE_TORCH_INDEX") or ROCM_TORCH_INDEX
    return None


def _installed_torch_is_rocm():
    """Ask a fresh interpreter: this process may already hold the old torch."""
    probe = "import torch, sys; sys.exit(0 if getattr(torch.version, 'hip', None) else 1)"
    try:
        return subprocess.call(
            [sys.executable, "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) == 0
    except OSError:
        return False


def rocm_torch_reinstall_cmd(index_url, python=None):
    """Build the uv argv that reinstalls only torch packages into this script's venv.

    A supplied ``python`` overrides the current interpreter. The ROCm source is
    an extra index, not a replacement for the default: forcing every dependency
    through PyTorch's index made resolution impossible (#3158). Keep this argv
    aligned with Electron's ``rocmTorchInstallArgs``.
    """
    tail = index_url.rstrip("/").rsplit("/", 1)[-1]
    tag = f"+{tail}" if re.fullmatch(r"rocm\d[\d.]*", tail) else ""
    names = [pin.split("==", 1)[0] for pin in ROCM_TORCH_PINS]
    return [
        "uv", "pip", "install",
        "--python", python if python else sys.executable,
        *(arg for name in names for arg in ("--reinstall-package", name)),
        *(f"{pin}{tag}" for pin in ROCM_TORCH_PINS),
        "--extra-index-url", index_url,
        "--index-strategy", "unsafe-best-match",
    ]


def _ensure_rocm_torch():
    """Replace the default torch with the ROCm build if asked to; never fatal."""
    index_url = _rocm_opt_in()
    if not index_url:
        return
    if _installed_torch_is_rocm():
        print("✓ torch in this environment already targets ROCm; nothing to swap")
        return
    print(f"⚙ OMNIVOICE_TORCH_VARIANT=rocm set: fetching AMD wheels from {index_url} ...")
    try:
        subprocess.check_call(rocm_torch_reinstall_cmd(index_url))
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"⚠ Could not switch to ROCm wheels ({err}); leaving the stock torch in place")
        return
    if not _installed_torch_is_rocm():
        # An untagged OMNIVOICE_TORCH_INDEX mirror can resolve the bare pins
        # to ordinary wheels.
        print("⚠ The installed torch has no ROCm support; check OMNIVOICE_TORCH_INDEX")
        return
    print("✓ ROCm wheels are now installed")


# ── Windows: VC++ Redistributable ─────────────────────────────────────────

def _ensure_vcredist_windows():
    """Check for and install the VC++ 2015-2022 Redistributable on Windows.

    PyTorch's native libraries (c10.dll, torch_cpu.dll, etc.) are built with
    MSVC and dynamically link against vcruntime140.dll + msvcp140.dll.  These
    ship with Visual Studio / Build Tools but are NOT part of Windows itself.
    On a fresh or debloated install the very first `import torch` crashes with:

        OSError: [WinError 126] The specified module could not be found.
        Error loading ...\\torch\\lib\\c10.dll or one of its dependencies.

    This function silently downloads and installs the official x64 redist
    package from Microsoft if the runtime DLLs are missing, except opted-in
    Windows ROCm setup, which requires the prerequisite to be installed manually.
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

    if _rocm_opt_in() is not None:
        raise RuntimeError(
            "Windows ROCm requires Microsoft Visual C++ 2015-2022 x64 Redistributable. "
            "Install it manually from https://aka.ms/vs/17/release/vc_redist.x64.exe, "
            "then rerun bun run setup:api. ROCm setup does not download this prerequisite."
        )

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


def main(argv=None):
    # ── Step 1: Windows VC++ Redistributable ──────────────────────────────
    _ensure_vcredist_windows()
    if argv == ["--windows-prerequisites-only"]:
        return

    # The shared CLI targets this interpreter and never imports native packages
    # into this parent before replacement. A requested Windows failure is fatal.
    if sys.platform == "win32" and _rocm_opt_in() is not None:
        subprocess.check_call(
            [sys.executable, "-m", "backend.core.windows_rocm.install", "--uv", "uv"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        return

    # macOS — no CUDA, nothing to do
    if sys.platform == "darwin":
        return

    # ── Step 2: AMD wheels on Linux, when requested ───────────────────────
    if sys.platform.startswith("linux"):
        _ensure_rocm_torch()

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
            [sys.executable, "-c", "import torch; print(torch.cuda.is_available() and not getattr(torch.version, 'hip', None))"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or result.stdout.strip() != "True":
            print("✓ No CUDA — cuDNN 8 compat not needed")
            return
    except Exception:
        print("⚠ CUDA probe failed — skipping optional cuDNN 8 compatibility libraries")
        return

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
    main(sys.argv[1:])
