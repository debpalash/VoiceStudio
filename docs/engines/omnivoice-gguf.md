# VoiceStudio — OmniVoice GGUF Engine

OmniVoice GGUF runs the same OmniVoice model as the [default
engine](omnivoice.md), but through a native binary
(`bin/omnivoice-tts-<platform>`) loading quantized GGUF weights. Native compute
adapts to your available backends; automatic quantization starts with the
smallest model to limit memory use on small GPUs and CPU-only hosts.

## When to pick it

- Your GPU is below the default engine's 6 GB VRAM floor.
- CPU-only machines that still want OmniVoice's voice and language coverage.
- You want generation isolated in a separate process (a crash or leak never
  takes the app down — each generation spawns the binary fresh).

## Quantization selection

Weights come from the `Serveurperso/OmniVoice-GGUF` HuggingFace repo, pinned
to an exact revision. Automatic selection uses Q4_K_M while the native process's
selected adapter capacity is unverified: the parent PyTorch GPU may be a
different adapter. Native GPU acceleration remains enabled. For an explicit
Settings override, these tiers provide sizing guidance:

| Hardware | Quant | Approx. VRAM use |
| --- | --- | --- |
| 12 GB+ VRAM | BF16 | ~1.6 GB (quality-first) |
| 4–12 GB VRAM | Q8_0 | ~945 MB (recommended balance) |
| 1–4 GB VRAM | Q4_K_M | ~659 MB (minimal footprint) |
| CPU-only | Q4_K_M | RAM-bound, latency-tolerable |

You can override the selection from Settings; overrides are allow-listed
against the same table (an F32 reference quant, ~3.2 GB, is override-only).

Which quant is actually loaded depends on what is installed:

- the selected tier is used whenever its pair is installed;
- an explicit Settings override is strict — only that exact pair counts, and a
  missing one is reported instead of silently running a different precision;
- an explicit CPU choice (Settings, `OMNIVOICE_DEVICE=cpu`,
  `OMNIVOICE_GGUF_BACKEND=cpu` or a native `GGML_BACKEND=CPU`) keeps the CPU
  tier's quant policy, so a larger cached quant is never used in its place;
- only when the selected pair is absent, and the selection is not an explicit
  CPU choice, does the smallest complete cached automatic pair (Q8_0, then
  BF16) stand in. Readiness and generation never download a replacement, and
  an installed quant is never rewritten.

## Weights

The engine never downloads weights. It loads the pinned base+tokenizer pair
from your local HuggingFace cache, and reports itself as *Unavailable* when
that pair is missing. The Model Catalogue then shows a generic "not installed
yet" line and a link to this guide — the pinned command lives here, not in the
catalogue row.

Install the default (Q4_K_M) pair once:

```bash
hf download Serveurperso/OmniVoice-GGUF \
  omnivoice-base-Q4_K_M.gguf omnivoice-tokenizer-Q4_K_M.gguf \
  --revision 361609388ae572a820d085185bbbe2a2aac4b30e \
  --cache-dir "<the cache directory VoiceStudio uses>"
```

The revision is the pin recorded in
`backend/engines/omnivoice_gguf/quant_map.json` (`_meta.source_commit_sha`).
The other tiers use the same command with their own filenames: Q8_0 is
`omnivoice-base-Q8_0.gguf` + `omnivoice-tokenizer-Q8_0.gguf`, and BF16 is
`omnivoice-base-BF16.gguf` + `omnivoice-tokenizer-BF16.gguf`.

### `--cache-dir` must be the app's cache

A standalone `hf` command does not inherit the cache VoiceStudio reads, so a
download that lands in the CLI's own default leaves the app without the
weights. Point `--cache-dir` at the directory the app actually uses — do not
move the app's cache to match the CLI.

**Windows, no cache overrides.** VoiceStudio redirects the cache to a short
path so deep model paths stay under the Windows path limit. PowerShell:

```powershell
hf download Serveurperso/OmniVoice-GGUF `
  omnivoice-base-Q4_K_M.gguf omnivoice-tokenizer-Q4_K_M.gguf `
  --revision 361609388ae572a820d085185bbbe2a2aac4b30e `
  --cache-dir "$env:LOCALAPPDATA\OmniVoice\hf_cache"
```

**A cache you configured.** The models directory in Settings (Storage) is
persisted as `OMNIVOICE_CACHE_DIR` and becomes the app's hub cache root;
`HF_HUB_CACHE` does the same. Use the same directory:

```powershell
hf download Serveurperso/OmniVoice-GGUF `
  omnivoice-base-Q4_K_M.gguf omnivoice-tokenizer-Q4_K_M.gguf `
  --revision 361609388ae572a820d085185bbbe2a2aac4b30e `
  --cache-dir "<your models directory>"
```

**macOS and Linux.** Without overrides the app uses the hub's own default
cache (`~/.cache/huggingface/hub`), which is also the CLI's default — pass
`--cache-dir` anyway if you set `OMNIVOICE_CACHE_DIR`, `HF_HUB_CACHE` or
`HF_HOME` (with only `HF_HOME` set, the hub's cache root is
`<HF_HOME>/hub`).

**Source checkouts.** Start the backend with the same cache the download
wrote to (`HF_HUB_CACHE` or `OMNIVOICE_CACHE_DIR`), so both sides agree.

The redirect applies only when nothing overrides it: no `OMNIVOICE_CACHE_DIR`,
no `HF_HOME`, no `HF_HUB_CACHE`. Set any of them and the app uses that
location instead.

## Setup

The desktop installers (Windows, macOS, Linux packages) do **not** ship this
native binary yet, so on an installed app the engine shows *Unavailable* in
Model Catalogue ([#2780](https://github.com/debpalash/VoiceStudio/issues/2780)).
Use the default in-process OmniVoice engine, which is the same model, or run
from a source checkout and build the binary for your platform (below). Once
`bin/omnivoice-tts-<platform>` exists, install the weights (see
[Weights](#weights)) and select the engine via **Model Catalogue** (TTS tab →
**Use**) or `OMNIVOICE_TTS_BACKEND=omnivoice-gguf`. Both the runtime binary
and the weight pair must be present before the engine reports itself as
available — a missing pair is reported as "not installed yet", with a link to
this guide, not with a download that starts behind your back.

**Source checkouts:** the repo ships zero-byte placeholders in `bin/` — real
binaries come from CI (`build-omnivoice-tts`) or a local build. The engine detects a placeholder and
reports unavailable with instructions
([#1172](https://github.com/debpalash/VoiceStudio/issues/1172)) instead of
failing at spawn time; build one with
`scripts/build-omnivoice-tts.sh --platform <slug>` or use the default
in-process engine.

**Linux ARM64 (Asahi Apple Silicon):** the `linux-aarch64` binary prefers
GGML's Vulkan backend when built on a host with `glslc` and the Khronos
SPIRV headers installed (Arch: `pacman -S shaderc spirv-headers`; Debian:
`apt install glslc libvulkan-dev spirv-headers`), so Apple GPUs accelerate
generation through the open-source Honeykrisp driver. Without those deps the
build falls back to CPU. Expect roughly 2–4x slower generation than macOS
Metal while upstream Mesa and llama.cpp Vulkan optimizations mature; still
well ahead of CPU-only.

## Optional Vulkan acceleration

Windows x64 and Linux x64/ARM64 source builds try Vulkan when the Vulkan SDK
(Windows), or `glslc` and SPIRV headers (Linux), are available. Missing build
dependencies or a failed Vulkan build fall back to CPU. The GPU backend ships
as an optional GGML module beside the executable; keep those libraries with the
binary. Install a working Vulkan GPU driver on the runtime host. Without one,
automatic native backend selection retains its CPU path.

Dynamic Vulkan builds disable host-native CPU flags and include runtime-selected
CPU variants, so the optional GPU module can configure against the pinned GGML
runtime while preserving optimized CPU fallback. This shares CPU dispatch flags
with [#3075](https://github.com/debpalash/VoiceStudio/pull/3075); that PR separately
addresses CPU-only builds and packaging.

The pinned runtime selects its own GPU independently of PyTorch. It cannot
list its selected device, so VoiceStudio conservatively reports an unverified
native device and uses the longer CPU generation budget. This does not disable
native GPU acceleration. A Vulkan loader alone does not prove GPU support.

Set `OMNIVOICE_GGUF_BACKEND` to `auto` (default), `cpu`, `cuda`, or `vulkan`.
For CUDA/Vulkan, `OMNIVOICE_GGUF_DEVICE` selects a non-negative device index
(default `0`). These map to the pinned runtime's `GGML_BACKEND` environment
variable (`CPU`, `CUDA0`, `Vulkan0`, etc.); its CLI has no `--backend` or
`--device` flags. Leave both unset for native automatic selection, including
Metal on supported macOS builds. Settings' CPU compute choice, or
`OMNIVOICE_DEVICE=cpu`, takes precedence over all native GPU overrides. Any
explicit CPU choice also selects the CPU quant (Q4_K_M) and requires that
pair to be installed — a larger cached quant is not substituted for it. An
explicit quant override remains authoritative in either case.

## Integrity and self-healing

Before reporting ready, the engine:

- verifies the binary against the SHA-256 manifest (`bin/checksums.sha256`);
- confirms the pinned base+tokenizer pair is present in the local cache (both
  files, at the pinned revision) — a half-installed pair does not count, and
  the engine never downloads weights;
- detects macOS Gatekeeper quarantine and prints the exact
  `xattr -cr '/Applications/VoiceStudio.app'` fix;
- restores a missing execute bit (a git clone or zip extract on POSIX can
  drop `+x`, which used to surface as a permission error mislabeled as
  out-of-memory — [#437](https://github.com/debpalash/VoiceStudio/issues/437)).
  The chmod runs only after the SHA check confirms it's the right file.

## Behaviour notes

- Output is 24 kHz mono — same model, same rate as in-process OmniVoice.
- Cloning from a reference clip (with optional transcript) and style
  instructions are supported; no voice design.
- Same multilingual surface as OmniVoice ([languages.md](../languages.md)).
- Because generation runs in another process, the app's own GPU counters
  don't see its allocations — diagnostics label it accordingly.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_GGUF_GENERATE_TIMEOUT_S` | (generous built-in) | Per-generation timeout for the spawned binary |

## Troubleshooting

- "GGUF binary missing": the installer doesn't include the runtime (see
  Setup) — use the default engine, or build the binary from a source checkout.
  The log line `Engine health check failed; engine=omnivoice-gguf` withholds the
  reason by design; a missing binary is the usual cause on an installed app.
- "GGUF weights are not installed": the runtime binary is there but the pinned
  weight pair is not. Follow the install steps in [Weights](#weights); the app
  never fetches weights for you, and the download has to land in the app's
  own cache.
- Checksum mismatch or quarantine messages: follow the printed fix, or
  reinstall.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[performance.md](../performance.md), [disk usage](disk-usage.md).
