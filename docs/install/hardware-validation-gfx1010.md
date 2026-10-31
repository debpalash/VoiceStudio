# gfx1010 (RDNA 1) on Windows ROCm 10 — hardware validation

Raw evidence from the machine this change was developed and measured on, kept
separate from `docs/install/windows-rocm.md` because it is a record of one
machine's run rather than a contract. Nothing here is extrapolated from a
different card, and every number was produced by a command whose outcome is
quoted beside it.

## Hardware and software

| Item | Value |
| --- | --- |
| GPU | AMD Radeon RX 5700 (Navi 10), `gfx1010:xnack-` |
| PCI device id | `1002:731f` (subsystem `1043:057d`), reported by `Win32_VideoController` |
| VRAM | 7.98 GiB usable |
| Driver | `32.0.21045.16010` |
| CPU | AMD Ryzen 5 5600 (6-core) |
| RAM | 15.94 GiB |
| OS | Windows 11 x64 |
| Python | 3.12.10 (cp312, win-amd64) |
| ROCm wheel stack | `rocm==10.0.0`, `rocm-sdk-core==10.0.0`, `rocm-sdk-libraries==10.0.0`, `rocm-sdk-device-gfx1010==10.0.0` |
| Torch stack | `torch==2.13.0+rocm10.0.0`, `torchaudio==2.11.0.2+rocm10.0.0`, `torchvision==0.28.0+rocm10.0.0` |
| Device wheels | `amd-torch-device-gfx1010==2.13.0+rocm10.0.0`, `amd-torchvision-device-gfx1010==0.28.0+rocm10.0.0` |
| `torch.version.hip` | `7.15.26333` |

The HIP string being `7.15.x` while the ROCm package version is `10.0.0` is
TheRock's own numbering, not a mismatch: the recipe pins `hip_prefix: "7.15."`
for this reason and verification compares the runtime's string, not the package
version.

Compiled architecture list reported by the wheel (`torch.cuda.get_arch_list()`):

```
gfx1010 gfx1011 gfx1012 gfx1030 gfx1031 gfx1032 gfx1033 gfx1034 gfx1035 gfx1036
gfx1100 gfx1101 gfx1102 gfx1103 gfx1150 gfx1151 gfx1152 gfx1153 gfx1200 gfx1201
gfx908 gfx90a
```

The base `torch` wheel lists every architecture, but the kernels only exist once
the matching `rocm-sdk-device-*` / `amd-torch-device-*` package is installed —
which is why `device_caps.build_arch_list()` filters against installed device
packages instead of trusting the wheel's list.

## Model-free kernel matrix

Every row was executed on the GPU with synchronisation and a result check; a
row is only "pass" when the output was finite and numerically sane.

| Check | Result |
| --- | --- |
| `fp32` matmul 512×512 | pass, max abs error `0.000103` vs a float64 CPU reference |
| `fp16` matmul 512×512 | pass, max abs error `0.0312` |
| `bf16` matmul 256×256 | pass, output finite |
| Conv1d fwd+bwd (fp16, 64ch, k3) | pass, grad finite |
| Grouped Conv1d fwd+bwd (groups=8) | pass |
| ConvTranspose1d fwd+bwd (k4, s2) | pass |
| 256 MiB allocation + fill | pass, peak `0.28 GiB` |

`bf16` matmul was additionally repeated 25× at 2048×2048 and 5× at sizes
1024/2048/4096/1×4096/256×8192 to look for the delayed rocBLAS crash that
`gfx103*` shows (#2372). All outputs stayed finite.

Known non-fatal warning printed once per process on this GPU:

```
MIOpen: Warning [OpenRuntimeLibraryForDevice] CK grouped conv library not found
for device gfx1010: 找不到指定的模块。
```

MIOpen falls back to its non-CK path and the grouped convolution above still
passes, so this is recorded rather than treated as a failure.

## Product probe

`python -m backend.core.windows_rocm.probe` (production code, no mocks), with
the recipe selecting itself from the host's PCI id:

```json
{"ready": true, "scope": "model-free", "architecture": "win-amd64", "abi": "cp312",
 "packages": {"torch": "2.13.0+rocm10.0.0", "torchaudio": "2.11.0.2+rocm10.0.0",
              "torchvision": "0.28.0+rocm10.0.0", "rocm": "10.0.0",
              "rocm-bootstrap": "0.3.0", "rocm-sdk-core": "10.0.0",
              "rocm-sdk-libraries": "10.0.0", "rocm-sdk-device-gfx1010": "10.0.0",
              "amd-torch-device-gfx1010": "2.13.0+rocm10.0.0",
              "amd-torchvision-device-gfx1010": "0.28.0+rocm10.0.0"},
 "hip": "7.15.26333", "torch_gpu": true, "kernel_result": 4.0,
 "gpu_architecture": "gfx1010", "ct2_supported": false,
 "ct2_gpu_count": 0, "ct2_compute_types": [], "ct2_binary_sha256": null}
```

Exit code 0. `ct2_supported: false` is the honest part: see the next section.

## CTranslate2 does not work on ROCm 10 (the load-bearing finding)

The pinned HIP CTranslate2 **4.8.2** is built against the ROCm 7.2.1 SDK. On the
ROCm 10 overlay it *loads* and misleads every cheap check:

```
ct2 4.8.2
get_cuda_device_count() -> 1
get_supported_compute_types('cuda') -> ['bfloat16','float16','float32','int8',
                                        'int8_bfloat16','int8_float16','int8_float32']
```

Real inference on the same interpreter then fails at kernel launch:

| compute type | native device loaded | inference result |
| --- | --- | --- |
| `float16` | `cuda`, `float16` | `RuntimeError: parallel_for failed: hipErrorInvalidImage: device kernel image is invalid` |
| `int8_float16` | `cuda`, `int8_float16` | `RuntimeError: parallel_for failed: hipErrorInvalidKernelFile: invalid kernel file` |
| `float32` | `cuda`, `float32` | `RuntimeError: parallel_for failed: hipErrorInvalidKernelFile: invalid kernel file` |

Because device discovery and the float16 claim both succeed, a recipe that
reused the 7.2.1 CT2 would pass readiness and fail every transcription. The
ROCm 10 overlay therefore pins no CT2, reports `ct2_supported: false`, and
`hip_ct2_available()` returns `False` without probing, so faster-whisper and
diarization stay on the CPU. Debugging that probe revealed two unrelated
packaging issues worth recording for anyone repeating it: `faster-whisper`
needs a `huggingface-hub` new enough for `metadata_errors`, and PyAV 19 removed
the `metadata_errors` keyword `faster_whisper.audio` still passes, so a direct
numpy waveform must be supplied to reach the model at all.

## Real speech synthesis (the end-to-end claim)

`OmniVoice.from_pretrained(<local snapshot>, device_map="cuda",
dtype=torch.bfloat16, load_asr=False)` — the same class the backend loads —
followed by a voice-cloning generation with the bundled demo reference:

| Measurement | Run 1 | Run 2 |
| --- | --- | --- |
| model load | `3.29 s` (`7.72 s` cold) | `3.41 s` |
| VRAM after load | `1.89 GiB` | `1.89 GiB` |
| generation (16 steps) | `28.38 s` → `7.09 s` of audio | `20.07 s` → `7.19 s` of audio |
| peak VRAM during generation | `2.34 GiB` | `2.34 GiB` |
| output | 24 000 Hz mono, peak `0.8148` FS, all samples finite | 24 000 Hz mono, peak `0.7770` FS, all finite |

Two runs are reported rather than one because they differ by 8 s on identical
settings — the render is not deterministic in wall-clock time and a single
number would misrepresent it. Peak VRAM and audio properties were identical
across runs, so those are stable.

The test text was a single neutral English sentence. No transcript, private
audio or personal voice material is included in this file.

For scale, the same machine running the same model on the ROCm 7.2.1 / torch
2.11 stack in the migrated install produced a 5.98 s generation in `6.03 s`
total render time. The 2.13.0 stack here is slower on this card; that is a
measurement, not a regression claim, because the two runs differ in torch
version, prompt length and step count.

## What is NOT validated here

- No packaged/installed Electron build was exercised on this GPU.
- No `gfx1011` / `gfx1012` hardware: those device wheels exist on AMD's index but
  are deliberately outside the recipe, so an RX 5500/5600 XT class card keeps CPU
  routing rather than an unverified GPU path.
- No ASR/diarization on GPU, for the reason above.
- No multi-GPU or mixed-AMD host: selection refuses those by design.
- Windows 10 and Windows-on-ARM are out of scope, as the install guide states.

## Reproducing

```powershell
# 1. Select the ROCm variant on a Windows x64 Python 3.12 checkout
$env:OMNIVOICE_TORCH_VARIANT = 'rocm'
bun run setup:api

# 2. Model-free probe (must print "ready": true)
& .\.venv\Scripts\python.exe -m backend.core.windows_rocm.install --verify-only

# 3. Real synthesis through the app
bun run dev:api          # then use the UI, or POST /generate as the app does
```

## Antivirus note (false positive, not a delivery problem)

While validating this change, Huorong (火绒) quarantined one bytecode file five
times and popped a notification each time:

```
TrojanDownloader/Python.Agent.ac
<checkout>\tests\__pycache__\test_cla_check.cpython-312-pytest-9.1.1.pyc
```

It is a false positive on a build artifact, and worth recording because the
symptom ("the project is flagged as a trojan downloader") is alarming while the
cause is mundane:

- `tests/test_cla_check.py` is upstream code (first added in `8eb3d9b1c`,
  last touched by `aacbb78e3`) and unmodified here — `git status` shows no
  change to it. Its bytecode merely contains the strings `base64`, `urllib`
  and `http` because it *tests* the CLA bot's comment rendering.
- Only the `.pyc` was flagged, never the `.py`. The hash matched exactly
  (`80A8479F…`, 104 740 bytes), and the source `.py` was never quarantined.
- No file was actually removed: the same `.pyc` still imported cleanly and its
  77 tests passed after every "quarantine" event. The AV logged a treatment
  event without deleting.
- It repeated because `pytest -k "rocm or windows"` still **imports every test
  module** before filtering, and pytest rewrites `.pyc` files when they are
  older than their source — so each run recreated the exact flagged file. Only
  `tests/__pycache__` was affected; no project output, model or environment file
  was involved.

If this happens to you, either add `<checkout>\tests\__pycache__` (or the whole
checkout) to your security software's exclusion list, or run the tests without
writing bytecode:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest tests/ -q
```

Verified locally: with that variable set, the file is not recreated at all.
`__pycache__/` is git-ignored, so removing it does not touch the repository.

