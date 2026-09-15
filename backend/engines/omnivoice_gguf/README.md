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

| Compute class | Quant | Base size | Tokenizer size | Total VRAM | When auto-selected |
|---------------|-------|-----------|----------------|------------|---------------------|
| `high-vram`   | BF16  | 1.23 GB   | 373 MB         | ~1.60 GB   | 12 GB+ VRAM        |
| `mid-vram`    | Q8_0  | 656 MB    | 289 MB         | ~945 MB    | 4-12 GB VRAM       |
| `low-vram`    | Q4_K_M| 407 MB    | 252 MB         | ~659 MB    | 1-4 GB VRAM        |
| `cpu`         | Q4_K_M| 407 MB    | 252 MB         | ~659 MB    | CPU-only fallback  |

F32 is published by the model but not auto-selected by the probe — it's
~3.2 GB total and offers no quality gain over BF16. Available as a
Settings override for users on machines where bit-exact reference output
is needed.

## Hardware probe

`hardware_probe.detect_capabilities()` walks:

1. `torch.cuda.is_available()` → CUDA backend, total VRAM from
   `torch.cuda.mem_get_info()`.
2. `torch.backends.mps.is_available()` → MPS backend, effective VRAM =
   half of system RAM (unified-memory bookkeeping).
3. Otherwise → CPU backend, VRAM = 0.

Thresholds (in `_bucket()`): 12 GB → high-vram, 4 GB → mid-vram,
1 GB → low-vram, else cpu.

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
voice cloning on Apple Silicon out of the box with `VoiceStudioBackend`
remaining available as an in-process fallback.

## Smoke test

`scripts/smoke-gguf.sh --hardware-class {cpu,mid,high}` runs the
GGUF-06 cross-hardware acceptance test. See the script's `--help` for
details.
