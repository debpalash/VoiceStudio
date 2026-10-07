# Maintaining the native Windows ROCm recipe

This is a contributor contract, **not a full AMD support declaration**. PR #2600
contains the opt-in source bootstrap and standalone offline smoke only. Desktop
integration and application-engine validation are separate review scopes. Do not
merge a broad workstation checkpoint in place of those focused changes.

## One reviewed recipe

`scripts/windows-rocm-recipe.json` is the data-only source of Windows Python,
Torch-family pins, AMD wheel source, CT2 release/member/checksums and HIP DLL
markers. `scripts/setup.py` resolves it relative to its own file, not the shell's
working directory. Keep the JSON beside the script when distributing source.
No dependencies or network access are needed to read it.

Desktop integrations should import this same file at build time, not keep
another list of Windows pins or download a mutable online manifest. The source-only
PR does not modify Electron; consuming this contract is a separate integration.
Vite embeds the recipe in the main-process bundle, so the installed app need not
read a source checkout. Linux ROCm, default CUDA, macOS and CPU recipes remain
unchanged; do not apply the Windows 2.9 stack to those platforms.

Treat the recipe as one reviewed unit. For an update:

1. Check AMD's OS/GPU/driver/Python matrix and the matching Torch, torchaudio,
   torchvision and ROCm wheels. A package version alone is not evidence of
   support for every Radeon.
2. Obtain CT2's **Windows HIP** release, verify the archive and exact wheel member
   independently, and update both SHA-256 values. A same-version PyPI/CUDA wheel
   is not equivalent. Inspect required native DLL imports again when ABI changes.
3. Update the recipe, provenance and user-facing prerequisites together. Check
   all consumers and the source and desktop tests; do not work around a failed
   checksum by accepting arbitrary artifacts. New download endpoints require
   maintainer approval under `.github/CONTRIBUTING.md` before merge.
4. Revalidate on actual supported hardware. Keep version/driver/GPU, exit code
   and scope in evidence; never infer a speech-engine pass from a matmul alone.
5. Check that unchanged recipes reuse valid environments, while changed Python,
   pins or artifacts invalidate readiness. A repair must be user-started, retain
   models/projects, and not silently downgrade HIP packages through `uv sync`.

## Tests without a Radeon

Use the test environment already provisioned for the checkout. Do **not** run
`uv sync` in a working AMD environment merely to run these tests. Set
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `HF_HUB_CACHE` to an empty directory.

```text
python -m pytest tests/test_windows_rocm_recipe.py tests/test_setup_rocm_variant.py tests/test_windows_rocm_smoke.py tests/test_rocm_torch_pins_match_pyproject.py -q
```

The recipe tests check version/ABI/hash structure, script-relative loading with
space/Unicode working directories, and propagation of a changed recipe into
setup. Setup and smoke tests cover opt-in, matching packages, wrong/native
artifacts, DLL handle lifetime, bounded WAV input, failure and optimized Python.
These are deterministic regression tests, **not hardware compatibility tests**.

For the separate Electron integration also run its `runtime-project.test.ts`,
`tests/test_electron_rocm_probes.py`, typecheck and packaging contracts. Test
default/Linux/macOS branches as well as Windows opt-in; no GPU is required for
the mocked contract tests. CI approval remains necessary for fork workflows.

## Hardware and release gates

Run `scripts/smoke_windows_rocm.py` directly with the intended interpreter.
Torch-only success proves synchronized HIP execution. Supply both an existing
local model and a WAV to test faster-whisper/CT2 speech inference offline.
Audio decoding/feature preparation can use CPU just as on CUDA; model inference
must actually use the Radeon. Content-free smoke reports do not assess transcript
quality; compare that separately with a reference.

Before calling a desktop installer ready, validate a clean Windows installation,
space/Unicode paths, supported driver/card, interrupted setup/retry, unchanged
runtime reuse, actual installed-app inference, offline restart, cancellation,
project recovery, export, uninstall, and keyboard/NVDA operation. Preserve the
previous release and user data before updating. Tests on one existing developer
machine with caches and Build Tools are not substitutes for that matrix.

WhisperX/pyannote need their own gate. Torchaudio compatibility is independent of
MIOpen's runtime-compiled LSTM kernels. ROCm library wheels alone omit rocRAND
headers needed by the tested MIOpen path. A development-header experiment is not
automatic SDK provisioning; do not set global environment variables, disable
MIOpen process-wide, modify installed dependencies, or advertise the full engine
based on an import or VAD-only pass. Gated diarization bundles require the user's
own access/license decision. Header installation, space/Unicode paths and the
full diarization pipeline remain unverified release requirements.

## Contribution boundaries

- Bootstrap/recipe/smoke: small independent source PR, with docs and regression
  tests; no Electron auto-detection or engine enablement implied.
- Audio-library compatibility: a tested application boundary, not a fork of
  site-packages; unchanged older stacks must remain no-ops.
- Desktop runtime: consume the recipe, verify package identity and GPU capability,
  stamp the complete contract, preserve data and require explicit installation.
- Engine integration: per-engine real inference and quality evidence before
  declaring support. Keep unsupported states visible; no silent CPU success.

The contributor CLA, maintainer approval of fork CI, and approval of AMD/CT2
download endpoints are distinct gates. A green bot review is none of those.
