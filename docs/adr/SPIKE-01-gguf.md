> **Archival note (2026-07-12):** moved here from `.planning/` in the root cleanup.
> Internal `.planning/` / `specs/` paths below are historical — those trees were removed; see git history.

# SPIKE-01: Adopt `Serveurperso/OmniVoice-GGUF` as hardware-adaptive default cloning engine

**Status:** Proposed (research-supported) — Wave 1 build/smoke flips to Accepted in Task 3
**Date:** 2026-05-18 (updated 2026-05-20 with pinned SHAs)
**Decision-makers:** [maintainer]
**Related:** ROADMAP Phase 4; REQUIREMENTS GGUF-01..06; `.planning/phases/04-adaptive-specialty-engines-spike-first/04-RESEARCH.md`

## Context

VoiceStudio v0.2.7 ships `k2-fsa/OmniVoice` (Apache-2.0, 0.6B Qwen3 backbone, Higgs Audio v2 codec at 24 kHz mono) as its default voice-cloning engine via `backend/services/tts_backend.py:VoiceStudioBackend`. The Python in-process path requires PyTorch + CUDA / MPS / CPU and on 4 GB-VRAM GPUs falls back to CPU inference.

`Serveurperso/OmniVoice-GGUF` (HuggingFace, 10,960 downloads/month, verified 2026-05-20) publishes 4 quantizations of the same upstream model — Q4_K_M (~659 MB VRAM), Q8_0 (~945 MB, recommended balance), BF16 (~1.6 GB), F32 (~3.2 GB) — consumable through the MIT-licensed `omnivoice.cpp` runtime (`github.com/ServeurpersoCom/omnivoice.cpp`, 42 stars, 59 commits, 6 open issues at the pinned SHA). The quants use a custom `omnivoice-lm` architecture (confirmed via HF API `gguf.architecture`) and do **not** load in vanilla llama.cpp.

This decision is whether to integrate the GGUF engine as a hardware-adaptive default with overridable fallback to the existing in-process `VoiceStudioBackend`.

## Decision

**GO** — integrate per GGUF-01..06.

The integration shape is `VoiceStudioGGUFBackend(TTSBackend)` wrapping Phase 2's `SubprocessBackend`, which spawns a bundled per-platform `omnivoice-tts` binary built from a pinned `omnivoice.cpp` commit SHA. Quant selection is driven by a `detect_capabilities()` extension of `backend/services/gpu_sandbox.py` mapping `(compute_class) → quant filename` via shippable `quant_map.json`. On hardware where probe + load succeed, GGUF becomes the default cloning engine; on any failure the existing in-process `VoiceStudioBackend` is the fallback.

### Spike verification (2026-05-20)

| Question | Verdict | Evidence |
|----------|---------|----------|
| Is the model the intended artifact (a quantization of `k2-fsa/OmniVoice`, not an overloaded "VoiceStudio" name)? | YES | HF model card explicitly chains: `Qwen/Qwen3-0.6B-Base → Qwen/Qwen3-0.6B → k2-fsa/OmniVoice → Serveurperso/OmniVoice-GGUF`. `base_model:k2-fsa/OmniVoice` and `base_model:quantized:k2-fsa/OmniVoice` tags present in the API response. |
| License compatible with v0.3.x ship? | YES — Apache-2.0 (model) + MIT (runtime) | Both verified via HF model card + GitHub README. Same Apache-2.0 chain as the upstream model already shipping in v0.2.7. |
| Runtime: llama.cpp / candle / custom? | CUSTOM (`omnivoice.cpp`, MIT) — does NOT load in vanilla llama.cpp | `gguf.architecture = "omnivoice-lm"` from HF API; README states "GGUF weights for omnivoice.cpp, a C++17/GGML port of VoiceStudio". |
| Quant variants and footprints? | 4 quants × 2 files each (base + tokenizer): Q4_K_M (659 MB), Q8_0 (945 MB), BF16 (1.60 GB), F32 (3.19 GB) | HF `siblings` list confirms all 8 files; sizes from model card table. |
| Cross-platform runtime fit? | Linux + Windows + macOS Intel YES via documented build scripts; macOS Apple Silicon Metal YES (builds clean via `cmake -DGGML_METAL=ON` at pinned SHA, #2105) | `buildcpu.sh`, `buildcuda.sh`, `buildvulkan.sh`, `buildall.sh` listed; Metal builds cleanly via `cmake -DGGML_METAL=ON` in CI and locally (#2105). |
| Subprocess CLI fits Phase 2 `SubprocessBackend`? | YES | README shows `echo "Hello world." | ./build/omnivoice-tts --model … --codec … --lang … -o …` — line-oriented stdin + argv + output-file pattern is exactly what `SubprocessBackend` is designed for. |

### Pinned SHAs (filled in Wave 1 by Task 1)

- `Serveurperso/OmniVoice-GGUF` HuggingFace repo revision SHA: `361609388ae572a820d085185bbbe2a2aac4b30e` (resolved 2026-05-20 via `curl https://huggingface.co/api/models/Serveurperso/OmniVoice-GGUF`; lastModified `2026-04-30T13:39:10.000Z`)
- `ServeurpersoCom/omnivoice.cpp` master HEAD SHA: `886fc079838ca7400cb2b42b36e2a65aa1daabe8` (resolved 2026-05-20 via `curl https://api.github.com/repos/ServeurpersoCom/omnivoice.cpp/commits/master`; commit `2026-05-17T12:41:06Z` — "cmake: scope /utf-8 to C and C++ so nvcc does not treat it as an input file")

Both SHAs are mirrored in `backend/engines/omnivoice_gguf/quant_map.json` `_meta` block so the engine code and the ADR cannot drift apart.

## Consequences

**Positive:**
- 4 GB-VRAM GPUs (currently falling back to CPU on the in-process path) get GPU-backed cloning via Q4_K_M.
- Smaller VRAM footprint = stays out of the way of other engines when users run multiple in one session.
- License chain unchanged (Apache-2.0 model + MIT runtime).
- Same underlying model as what already ships — worst case it ties the in-process path on a given hardware class and we keep that path as the fallback.

**Negative / risk:**
- Adds a maintained-by-others C++ runtime to the dependency graph (`omnivoice.cpp`, 42 stars at decision time).
- Adds ~12-16 MB of platform binaries to the installer (must verify against Phase 3 mirror-timing baseline per Pitfall 6).
- macOS code signing scope expands by 4 binaries (track via REL-05; same `xattr -cr` workaround as #54 applies in v0.3.x).
- `omnivoice.cpp` README does not publish a standalone `buildmetal.sh` script; Apple Silicon Metal is built directly via `cmake -DGGML_METAL=ON` (#2105).

**Mitigations:**
- Pin `omnivoice.cpp` by commit SHA (`886fc079838ca7400cb2b42b36e2a65aa1daabe8`); rebuild from pinned SHA in CI for all 4 target platforms.
- Pin every quant file by commit SHA in `quant_map.json` (`361609388ae572a820d085185bbbe2a2aac4b30e`); shippable JSON so the table can update without an app release.
- In-process `VoiceStudioBackend` remains as fallback if any GGUF step fails (probe, download, load, generate).
- macOS Apple Silicon Metal build is verified via `cmake -DGGML_METAL=ON` (#2105); in-process `VoiceStudioBackend` remains available as general fallback.
- SHA-256 checksums on bundled binaries (per GATE-05); verify at first launch and on every quant load.
- Subprocess arg composition uses typed `Path` objects rooted in app directories; quant override UI is a dropdown over `quant_map.json` entries only (no freeform path input — supply-chain control analogous to INST-09).

## Sources

- `.planning/phases/04-adaptive-specialty-engines-spike-first/04-RESEARCH.md` (this milestone's research)
- https://huggingface.co/Serveurperso/OmniVoice-GGUF (verified 2026-05-18 / re-verified 2026-05-20)
- https://huggingface.co/api/models/Serveurperso/OmniVoice-GGUF (SHA `361609388ae572a820d085185bbbe2a2aac4b30e`, 2026-05-20)
- https://github.com/ServeurpersoCom/omnivoice.cpp (verified 2026-05-18 / re-verified 2026-05-20)
- https://api.github.com/repos/ServeurpersoCom/omnivoice.cpp/commits/master (SHA `886fc079838ca7400cb2b42b36e2a65aa1daabe8`, 2026-05-20)
- https://huggingface.co/k2-fsa/OmniVoice (upstream, Apache-2.0)
- `backend/services/tts_backend.py` (existing `VoiceStudioBackend` reference)
