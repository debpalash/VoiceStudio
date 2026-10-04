# Native Windows ROCm: opt-in source bootstrap

This is an **experimental, source-only dependency recipe**, not a supported
Windows GPU mode for the desktop app. It replaces packages in a local source
checkout's Python environment only when `OMNIVOICE_TORCH_VARIANT=rocm` is set.
It does not enable Electron AMD auto-detection, modify the packaged installer,
change engine routing, or establish NVIDIA/AMD feature parity.

**The upstream backend still has GPU compatibility limitations.** Its normal
Torch constraints target 2.8; this recipe deliberately uses AMD's Windows 2.9.1
wheels outside those constraints. Bootstrap and standalone smoke checks do
not establish that VoiceStudio's WhisperX, faster-whisper, diarization or TTS
adapters work on the GPU. App compatibility is a separate requirement.

## Prerequisites

- Native **Windows 11 x64** and an AMD GPU explicitly listed in the
  [ROCm 7.2.1 Windows support matrix][matrix], for example Radeon RX 9070 XT.
  Do not infer support for every RX 7000/9000 card, integrated GPU, Windows 10,
  Windows ARM64, or unlisted Windows build from this example.
- AMD's **26.2.2 graphics driver**, required by its
  [7.2.1 Windows PyTorch installation guide][amd-install]. Follow that guide
  for installation and any reboot. Setup installs neither drivers nor the HIP
  SDK; required ROCm native libraries must be loadable by Python.
- **64-bit CPython 3.12**, `uv` on `PATH`, and a source checkout. Setup rejects
  other Python minor versions and non-`win-amd64` interpreters for this opt-in.
  The existing setup step checks the Microsoft Visual C++ 2015–2022 x64
  Redistributable and attempts to install it if missing.
- Network access for explicit provisioning: Python/dependencies, AMD's wheel
  page, and the official CTranslate2 GitHub release. This is not an offline
  installer. The standalone smoke below, unlike provisioning, is offline.

Use a separate development checkout. Do not target an installed application's
managed runtime or an environment whose existing packages you need to preserve.

## Exact source-only invocation

In PowerShell, from the **source checkout root**, not the installed app folder:

```powershell
$env:OMNIVOICE_TORCH_VARIANT = "rocm"
uv sync --frozen --no-dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw "Base dependency sync failed" }
uv run --no-sync --python 3.12 python scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw "Windows ROCm bootstrap failed" }
```

The opt-in applies to this PowerShell session; no persistent setting is needed.
Sync creates the normal frozen environment under Python 3.12. Setup then
replaces its Torch stack and CTranslate2 in that same interpreter. Its
`uv --no-config pip install` bypasses the project's incompatible Torch
constraints; this does **not** update `uv.lock` or certify the full dependency
graph. On failure, partial package replacement can remain; there is no rollback.

For direct Python diagnostics use `uv run --no-sync --python 3.12 python ...`
or the checkout's `.venv\Scripts\python.exe`. A later `uv sync`, or `uv run`
without `--no-sync`, restores locked packages; rerun the recipe after syncing.
Do not substitute `bun run setup:api`: its ordinary sync uses default Python
selection. Launching Electron is a separate flow, not validation of this venv.

## Pins and provenance

The machine-readable contract is `scripts/windows-rocm-recipe.json`. Keep it
beside `scripts/setup.py`; setup reads it relative to the script, independent of
the working directory. Maintainers should follow the
[update and validation contract](../maintainers/windows-rocm.md). A desktop port
can consume this same recipe at build time without duplicating version pins;
this source-only PR still does not change Electron's managed runtime.

| Package | Windows opt-in version |
| --- | --- |
| `torch` | `2.9.1+rocm7.2.1` |
| `torchaudio` | `2.9.1+rocm7.2.1` |
| `torchvision` | `0.24.1+rocm7.2.1` |
| `rocm[libraries]` | `7.2.1` |
| `ctranslate2` | `4.8.2`, official Windows HIP wheel, installed with `--no-deps` |

The Torch stack uses AMD's [`rocm-rel-7.2.1` page][amd-wheels] (`--find-links`).
`OMNIVOICE_WINDOWS_ROCM_FIND_LINKS` can override the page without changing
pins; `OMNIVOICE_TORCH_INDEX` remains the Linux override, not the Windows one.
These Torch wheels are version-pinned, not checked against a separately
maintained SHA-256 list. Setup reuses HIP Torch only if all four installed
distribution versions match the recipe; otherwise it reinstalls the stack.

CTranslate2 comes from the [official OpenNMT v4.8.2 release][ct2-release]:

- Archive: [`rocm-python-wheels-Windows.zip`][ct2-archive]
- Archive SHA-256: `43DA4BAA5FEAEE49F77E176277A9647F99C493173C81A0BC60F491CAC97532C2`
- Exact member: `temp-windows/ctranslate2-4.8.2-cp312-cp312-win_amd64.whl`
- Wheel SHA-256: `D5B9B0D34B23584638FC087BF99A85B0BA9917B84860D5FBDDCC73B0AEDD4FD4`

Setup verifies the archive before opening it and the wheel before installing
it. It accepts exactly one matching member and writes only that member, not
the archive's other paths. Hash mismatch, missing/duplicate member, install
failure or failed GPU probe stops setup. Temporary downloads/extractions are
cleaned up. A working CT2 install is probed and reused, not downloaded again
or re-attested against the release hash.

Setup probes in fresh Python subprocesses: HIP Torch, GPU availability and a
small tensor multiplication; CT2 version, HIP DLL import markers, device count
and advertised `float16` support through its `cuda` API (also used by HIP).
These are **bootstrap checks, not speech inference**. Check GPU, driver, Python
and DLL availability on failure; do not disable hashes or substitute NVIDIA
cuDNN or a CPU run to obtain a false success.

## Offline standalone smoke

Run with the environment being evaluated, without syncing it. From the source
root, this invocation checks a synchronized Torch GPU kernel only:

```powershell
.\.venv\Scripts\python.exe -B scripts/smoke_windows_rocm.py
```

For real ASR, supply **both** a complete local converted faster-whisper model
directory (`model.bin`, `config.json`, `tokenizer.json`) and a real mono/stereo
PCM speech WAV lasting at most 60 seconds (up to 128 MiB, 192 kHz, 32-bit).
Headers are checked against file size, and payload checks use bounded reads.
Replace these example paths with
your own existing inputs; no model is downloaded:

```powershell
.\.venv\Scripts\python.exe -B scripts/smoke_windows_rocm.py `
  --model-dir .\models\faster-whisper-small --wav .\samples\speech.wav `
  --compute-type float16 --timeout 180
```

The script forces Hugging Face/Transformers offline mode, does not import the
backend or compatibility wrappers, consumes the lazy transcription result,
and rejects CPU model fallback. `--device-index` selects a GPU; `--dll-dir`
adds an explicitly trusted local SDK DLL directory when needed. Audio decoding
and feature preparation still use CPU; the GPU requirement concerns inference.
Stdout is one JSON report without paths, transcripts or raw exceptions. Exit
0 means success for the requested scope, 1 means runtime failure, and 2 means
CLI usage error. No ASR inputs means `scope=torch_only`, never an ASR claim.
Native crashes/timeouts fail rather than count as a pass.

**Recorded evidence (2026-10-04):** an existing native Windows environment with
Python 3.12.14, Torch 2.9.1+rocm7.2.1, official CT2 4.8.2 and faster-whisper
1.2.1 passed on Radeon RX 9070 XT: synchronized matrix checksum 8192; local
small-model ASR of 4.8 seconds of speech completed on `cuda:0`/HIP, `float16`,
with one segment and exit 0 (`scope=torch_and_asr`). No dependency overlay or
backend wrapper was used for that run. This validates an **existing
environment**, not fresh provisioning with this recipe, engine parity,
transcription quality or the app's ASR adapters. Other cards/drivers remain
unverified. Mocked unit tests are separate from this hardware evidence.

## Unchanged defaults and opting out

- Without the explicit `rocm` variant (including unset, `auto`, `cpu`, `cuda`),
  Windows setup does not install this stack; ordinary VC++/cuDNN setup remains.
- Linux keeps its ROCm 6.4 / Torch 2.8.0 opt-in and existing failure behavior;
  macOS setup is unchanged. Windows never uses the Linux ROCm wheel index.
- This opt-in skips NVIDIA cuDNN installation, without deleting existing files.
- Packaged Electron keeps its existing CPU/NVIDIA runtime selection and GPU
  reporting. This recipe is not an app setting or support verdict.

To restore this **source checkout's** frozen default Python 3.11 packages
(not a backup of its previous environment):

```powershell
Remove-Item Env:OMNIVOICE_TORCH_VARIANT -ErrorAction SilentlyContinue
uv sync --frozen --no-dev --python 3.11
if ($LASTEXITCODE -ne 0) { throw "Default dependency sync failed" }
uv run --no-sync --python 3.11 python scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw "Default setup failed" }
```

See [Windows installation](windows.md) for the ordinary supported desktop flow.

[matrix]: https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/compatibility/compatibilityrad/windows/windows_compatibility.html
[amd-install]: https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2.1/docs/install/installrad/windows/install-pytorch.html
[amd-wheels]: https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/
[ct2-release]: https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.2
[ct2-archive]: https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Windows.zip
