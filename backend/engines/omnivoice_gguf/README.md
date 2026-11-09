# OmniVoice GGUF Engine

Hardware-adaptive quantized variant of the upstream `k2-fsa/OmniVoice`
voice-cloning model. Runs the C++17/GGML `omnivoice.cpp` runtime as a
subprocess (via Phase 2's `SubprocessBackend`) so the runtime is fully
isolated from the parent's Python process.

## License

- **Model weights** (`Serveurperso/OmniVoice-GGUF`): Apache-2.0. Same
  license as the upstream `k2-fsa/OmniVoice` it derives from.
- **Runtime** (`omnivoice.cpp`, the C++ inference binary in `bin/`): MIT.
- **Audio codec** (Higgs Audio v2, embedded in the tokenizer GGUF): Apache-2.0.

The license chain is identical to the engine VoiceStudio already
ships in v0.2.7 (`VoiceStudioBackend`); this engine is the same model
under a different runtime.

## Sources

- Model card: https://huggingface.co/Serveurperso/OmniVoice-GGUF
- Runtime source: https://github.com/ServeurpersoCom/omnivoice.cpp
- Decision doc: `docs/adr/SPIKE-01-gguf.md`
- Research: `docs/adr/SPIKE-01-gguf-research.md`

## Pinned SHAs

The `_meta` block of `quant_map.json` carries the pins:

- `source_commit_sha` — HuggingFace revision of `Serveurperso/OmniVoice-GGUF`
  used to download quant files. Currently `361609388ae572a820d085185bbbe2a2aac4b30e`.
- `runtime_commit_sha` — `omnivoice.cpp` master HEAD that the bundled
  `bin/omnivoice-tts-<platform>` binaries were built from. Currently
  `886fc079838ca7400cb2b42b36e2a65aa1daabe8`.

Both SHAs are mirrored in the SPIKE-01 ADR so the engine code and the
decision doc cannot drift.

## Quant variants

| Compute class | Quant | Base size | Tokenizer size | Total VRAM | Selection policy |
|---------------|-------|-----------|----------------|------------|------------------|
| `high-vram`   | BF16  | 1.23 GB   | 373 MB         | ~1.60 GB   | Manual override; 12 GB+ VRAM sizing guidance |
| `mid-vram`    | Q8_0  | 656 MB    | 289 MB         | ~945 MB    | Manual override; 4-12 GB VRAM sizing guidance |
| `low-vram`    | Q4_K_M| 407 MB    | 252 MB         | ~659 MB    | Manual option; 1-4 GB VRAM sizing guidance |
| `cpu`         | Q4_K_M| 407 MB    | 252 MB         | ~659 MB    | Automatic default while native capacity is unverified |

The automatic `cpu` tier selects the smallest quant without changing the native
compute backend: GPU acceleration remains available. Explicit allowed quant
filenames remain authoritative.

F32 is published by the model but requires an explicit override — it's
~3.2 GB total and offers no quality gain over BF16. Available as a
Settings override for users on machines where bit-exact reference output
is needed.

## Weights: installed first, downloaded on first use

`quant_cache.py` resolves the pinned base+tokenizer pair from the local
HuggingFace cache (`try_to_load_from_cache`, a pure filesystem lookup).
Generation (`_resolve_quant_paths`) uses an installed pair whenever one
is usable; only when none is does it download the selected pair at the
pinned revision with `hf_hub_download`. Readiness (`is_available`) checks
the runtime binary only, so a fresh install is ready before its weights
arrive.

- A complete pair counts only when **both** files exist at the pinned
  revision; a half-installed pair, a directory, a dangling symlink and a
  zero-byte partial download do not.
- The selected tier is used whenever its pair is installed. An explicit
  Settings override is strict — only that exact pair counts. An explicit
  CPU choice (Settings, `OMNIVOICE_DEVICE=cpu`, `OMNIVOICE_GGUF_BACKEND=cpu`
  or a native `GGML_BACKEND=CPU`) keeps the CPU tier's quant policy, so
  a larger cached quant is not substituted for it.
- Only when the selected pair is absent, and the selection is not an
  explicit CPU choice, does the smallest complete cached automatic pair
  (Q8_0, then BF16) stand in (#3042), so an existing installation is
  never re-downloaded, and an installed quant is never rewritten.
  Override-only quants (F32) are never auto-selected.

An offline host can pre-install a pair by hand; it has to land in the
cache this app reads — a standalone `hf` command does not inherit the
app's cache location:

```bash
hf download Serveurperso/OmniVoice-GGUF \
  omnivoice-base-Q4_K_M.gguf omnivoice-tokenizer-Q4_K_M.gguf \
  --revision 361609388ae572a820d085185bbbe2a2aac4b30e \
  --cache-dir "<the cache directory this app reads>"
```

The revision is `_meta.source_commit_sha` from `quant_map.json`;
`docs/engines/omnivoice-gguf.md` carries the per-platform `--cache-dir`
examples. A failed first-use download raises with this command in the
message.

## Runtime integrity

`bin/checksums.sha256` covers the executable and every ggml shared
library `scripts/build-omnivoice-tts.sh` copies next to it. Every listed
library that is present must match before the binary is spawned; an
unlisted one is logged (manifests from before #3042 list only the
executable). The binary runs with `bin/` as its working directory, so
ggml's dynamic backend loader cannot pick up a library from anywhere
else.

## Hardware probe

`hardware_probe.detect_capabilities()` describes the parent Python process:

1. `torch.cuda.is_available()` → CUDA backend, total VRAM from
   `torch.cuda.mem_get_info()`.
2. `torch.backends.mps.is_available()` → MPS backend, effective VRAM =
   half of system RAM (unified-memory bookkeeping).
3. Otherwise → CPU backend, VRAM = 0.

Thresholds (in `_bucket()`): 12 GB → high-vram, 4 GB → mid-vram,
1 GB → low-vram, else cpu.

That parent CUDA/MPS adapter may differ from the native runtime's selected
adapter, so its memory cannot establish a safe automatic quant size.
`compute.automatic_compute_class()` instead uses the conservative `cpu` tier
until native adapter capacity can be verified. Larger quants remain available
through explicit Settings overrides.

## Subprocess invocation

The engine spawns `bin/omnivoice-tts-<platform>` once per `generate()`
call. The CLI matches the `omnivoice.cpp` README:

```
echo "Hello world." | ./build/omnivoice-tts \
    --model models/omnivoice-base-Q8_0.gguf \
    --codec models/omnivoice-tokenizer-Q8_0.gguf \
    --lang English -o hello.wav
```

The Python wrapper composes argv from typed `pathlib.Path` objects rooted
in `$HF_HUB_CACHE`, never passes `shell=True`, and routes `stdin`/`stderr`
through `SubprocessBackend.run()` so HF token leak redaction (AUTH-05)
and the 120-second timeout (T-04-06) apply uniformly.

## Settings override

`backend/services/settings_store.py` exposes `get_quant_override()` and
`set_quant_override(value)`. Allowed values:

- `None` — clear override; auto-select per `quant_map.json`.
- `"auto"` — explicit auto-select (same as `None` but writes a marker
  row so the UI can show "user explicitly chose auto").
- One of the `base` filenames listed in `quant_map.json` — forces that
  quant regardless of hardware bucket.

Freeform path input is rejected with `ValueError` (T-04-05 in the
plan's threat model).

## macOS Gatekeeper

The bundled `bin/omnivoice-tts-darwin-*` binaries are not code-signed in
v0.3.x (REL-05 tracks signing as a separate work item). macOS Sequoia
Gatekeeper will quarantine them on first launch, surfacing the same UX
as the `.app` quarantine (issue #54).

The workaround is identical:

```
xattr -cr "/Applications/VoiceStudio.app"
```

This clears the quarantine xattr recursively — including on the bundled
`bin/omnivoice-tts-darwin-*` binary inside the `.app`. The
`is_available()` probe in `backend.py` detects the quarantine xattr and
returns a clear error message pointing at this command rather than
silently hanging on a Gatekeeper-killed spawn.

The macOS Apple Silicon Metal build compiles cleanly with `-DGGML_METAL=ON`
at the pinned `omnivoice.cpp` SHA (#2105), enabling GPU-accelerated GGUF
voice cloning when the packaged binary passes preflight and is permitted by
macOS. Missing binaries, placeholders, or Gatekeeper rejection leave
`VoiceStudioBackend` available as the in-process fallback.

## Smoke test

`scripts/smoke-gguf.sh --hardware-class {cpu,mid,high}` runs the
GGUF-06 cross-hardware acceptance test. See the script's `--help` for
details.
