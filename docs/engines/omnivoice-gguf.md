# VoiceStudio — OmniVoice GGUF Engine

OmniVoice GGUF runs the same OmniVoice model as the [default
engine](omnivoice.md), but through a native binary
(`bin/omnivoice-tts-<platform>`) loading quantized GGUF weights. It is
hardware-adaptive: a probe picks the quantization that fits your machine, so
small GPUs and CPU-only hosts get a working OmniVoice instead of a paging,
timing-out one.

## When to pick it

- Your GPU is below the default engine's 6 GB VRAM floor.
- CPU-only machines that still want OmniVoice's voice and language coverage.
- You want generation isolated in a separate process (a crash or leak never
  takes the app down — each generation spawns the binary fresh).

## Quantization selection

Weights come from the `Serveurperso/OmniVoice-GGUF` HuggingFace repo, pinned
to an exact revision. The hardware probe selects:

| Hardware | Quant | Approx. VRAM use |
| --- | --- | --- |
| 12 GB+ VRAM | BF16 | ~1.6 GB (quality-first) |
| 4–12 GB VRAM | Q8_0 | ~945 MB (recommended balance) |
| 1–4 GB VRAM | Q4_K_M | ~659 MB (minimal footprint) |
| CPU-only | Q4_K_M | RAM-bound, latency-tolerable |

You can override the selection from Settings; overrides are allow-listed
against the same table (an F32 reference quant, ~3.2 GB, is override-only).

## Setup

Nothing to install: the Windows x64, Linux x64 and Apple Silicon installers
bundle the runtime. Windows on ARM and Intel Macs don't, so they keep the
default in-process engine. Select the engine via **Model Catalogue** (TTS tab → **Use**) or
`OMNIVOICE_TTS_BACKEND=omnivoice-gguf`. The quant weights download on first
use (see [downloading-models.md](../downloading-models.md)) — install them
ahead of time from **Model Catalogue (TTS tab → the engine's Weights)** if you want the first
generation to be quick; a long first render is the download, not a hang.

**Source checkouts:** the repo ships zero-byte placeholders in `bin/` — real
binaries come from CI (`build-omnivoice-tts`) or a local build. The engine detects a placeholder and
reports unavailable with instructions
([#1172](https://github.com/debpalash/VoiceStudio/issues/1172)) instead of
failing at spawn time; build one with
`scripts/build-omnivoice-tts.sh --platform <slug>` or use the default
in-process engine.

**Every x86-64 CPU:** the `windows-x86_64`, `linux-x86_64` and
`darwin-x86_64` builds ship one ggml CPU library per instruction-set level,
from SSE4.2 to AVX-512, and load the best one for the processor at start. The
same binary runs on old and new CPUs instead of only on the CI machine that
built it ([#2780](https://github.com/debpalash/VoiceStudio/issues/2780)).
Keep the binary and its `ggml*` libraries together in `bin/`.

**Linux ARM64 (Asahi Apple Silicon):** the `linux-aarch64` binary prefers
GGML's Vulkan backend when built on a host with `glslc` and the Khronos
SPIRV headers installed (Arch: `pacman -S shaderc spirv-headers`; Debian:
`apt install glslc libvulkan-dev spirv-headers`), so Apple GPUs accelerate
generation through the open-source Honeykrisp driver. Without those deps the
build falls back to CPU. Expect roughly 2–4x slower generation than macOS
Metal while upstream Mesa and llama.cpp Vulkan optimizations mature; still
well ahead of CPU-only.

## Integrity and self-healing

Before reporting ready, the engine:

- verifies the binary against the SHA-256 manifest (`bin/checksums.sha256`);
- detects macOS Gatekeeper quarantine and prints the exact
  `xattr -cr '/Applications/VoiceStudio.app'` fix;
- restores a missing execute bit (a git clone or zip extract on POSIX can
  drop `+x`, which used to surface as a permission error mislabeled as
  out-of-memory — [#437](https://github.com/debpalash/VoiceStudio/issues/437)).
  The chmod runs only after the SHA check confirms it's the right file.
- reports a runtime that died with an illegal-instruction fault as unusable
  on this CPU until the file is rebuilt or reinstalled.

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

- "GGUF binary missing": this build doesn't include the runtime (Windows on
  ARM, Intel Mac, or a source checkout; see Setup) — use the default engine,
  or build the binary from a source checkout.
  The log line `Engine health check failed; engine=omnivoice-gguf` withholds the
  reason by design; a missing binary is the usual cause on such a build.
- Checksum mismatch or quarantine messages: follow the printed fix, or
  reinstall.
- "This CPU cannot run it" (illegal instruction): the runtime was compiled
  for instructions your processor lacks, usually a build from before #2780 or
  a native build copied from another machine. Rebuild it with
  `scripts/build-omnivoice-tts.sh` or reinstall VoiceStudio.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[performance.md](../performance.md), [disk usage](disk-usage.md).
