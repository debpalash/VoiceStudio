# VoiceStudio — OmniVoice GGUF Engine

OmniVoice GGUF runs the same OmniVoice model as the [default
engine](omnivoice.md), but through a native binary
(`bin/omnivoice-tts-<platform>`) loading quantized GGUF weights. Native compute
adapts to your available backends. On AMD and Intel GPUs it runs on Vulkan
with nothing to configure, and the quant is sized to the GPU it will use.

## When to pick it

- Your GPU is below the default engine's 6 GB VRAM floor.
- An AMD Radeon or Intel Arc GPU (or a Ryzen AI / Core Ultra iGPU), which
  PyTorch on Windows cannot use but this engine reaches through Vulkan.
- CPU-only machines that still want OmniVoice's voice and language coverage.
- You want generation isolated in a separate process (a crash or leak never
  takes the app down — each generation spawns the binary fresh).

## Quantization selection

Weights come from the `Serveurperso/OmniVoice-GGUF` HuggingFace repo, pinned
to an exact revision. Automatic selection sizes the quant on the GPU the
native runtime will open, found without PyTorch (see
[Vulkan acceleration](#vulkan-acceleration)):

| Hardware | Quant | Approx. VRAM use |
| --- | --- | --- |
| 12 GB+ VRAM with native BF16 | BF16 | ~1.6 GB (quality-first) |
| 4–12 GB VRAM, or 12 GB+ without native BF16 | Q8_0 | ~945 MB (recommended balance) |
| 1–4 GB VRAM | Q4_K_M | ~659 MB (minimal footprint) |
| CPU-only or unverified GPU | Q4_K_M | RAM-bound, latency-tolerable |

Integrated GPUs (Ryzen AI APUs such as the Radeon 890M, Intel Core Ultra
iGPUs) use shared system memory, so their 512 MB BIOS frame buffer is not
their limit: on a host with at least 16 GB of RAM they are sized on half the
RAM. A Radeon 890M on a 32 GB laptop therefore gets the 12 GB+ budget.

BF16 needs native bfloat16 shaders (`VK_KHR_shader_bfloat16`, shown as
`bf16: 1` in the `ggml_vulkan:` log line). Without them ggml-vulkan emulates
BF16: on a Radeon RX 6700 XT (`bf16: 0`) BF16 generated 1.8× slower than a
quantized model. So a 12 GB+ GPU gets BF16 only when `vulkaninfo` confirms the
extension; otherwise, including when only the OS GPU inventory was available,
it gets Q8_0. `OMNIVOICE_GGUF_QUANT=BF16` or the Settings picker still selects
BF16.

Without a verified Vulkan GPU (no Vulkan driver, a runtime built without the
Vulkan module, or a CUDA/Metal route the app cannot size) the selection stays
on Q4_K_M. The parent PyTorch GPU is never used as evidence: it may be a
different adapter.

**Choosing another quant.** Settings › Models › OmniVoice GGUF shows the
automatic pick, for example *Auto: Q8_0 (Vulkan – AMD Radeon 890M)*, and a
picker for Q4_K_M, Q8_0, BF16 and F32 (an ~3.2 GB reference quant that is
never picked automatically). `OMNIVOICE_GGUF_QUANT` (`Q4_K_M`, `Q8_0`, `BF16`,
`F32`, a full `omnivoice-base-*.gguf` filename, or `auto`) pins one from the
environment and takes precedence over Settings; the picker then shows the pin
and is locked.

Which quant is actually loaded depends on what is installed:

- the selected tier is used whenever its pair is installed;
- an explicit Settings override is strict — only that exact pair counts, and a
  missing one is reported instead of silently running a different precision;
- an explicit CPU choice (Settings, `OMNIVOICE_DEVICE=cpu`,
  `OMNIVOICE_GGUF_BACKEND=cpu` or a native `GGML_BACKEND=CPU`) keeps the CPU
  tier's quant policy, so a larger cached quant is never used in its place;
- only when the selected pair is absent, and the selection is not an explicit
  CPU choice, does the smallest complete cached automatic pair (Q8_0, then
  BF16) stand in, so an existing installation is never re-downloaded, and an
  installed quant is never rewritten.

## Weights

An installed base+tokenizer pair at the pinned revision is always used from
your local HuggingFace cache. When no usable pair is installed, the first
generate downloads the selected pair from Hugging Face at that pinned
revision, the same way every other engine fetches its model. To fetch it ahead
of time, or to get a bigger quant than the one installed, press **Download**
next to the quant picker in Settings › Models; it shows progress and saves into
the app's cache (`OMNIVOICE_CACHE_DIR` when set). Until the selected quant is
downloaded, the installed one keeps working and the picker says so. Nothing is
downloaded until you press Download or generate.

### Pre-installing on an offline host

On a machine that cannot reach Hugging Face from the app, install the
default (Q4_K_M) pair once by hand:

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

The desktop installers ship this native binary under `resources/bin/`
([#2780](https://github.com/debpalash/VoiceStudio/issues/2780)): Windows x64,
Windows on ARM (the x64 binary, run under emulation like the rest of the local
backend), macOS Apple Silicon and the Linux AppImage/deb. The Windows binary
carries its own Visual C++ runtime, so no redistributable install is needed.
The Intel Mac download has no binary because Intel Macs cannot run the local
backend at all ([macOS install](../install/macos.md)). Store builds (Microsoft
Store, Mac App Store, Snap Store) and the Docker image do not include it yet;
use the default in-process OmniVoice engine there, which is the same model.

Select the engine via **Model Catalogue** (TTS tab → **Use**) or
`OMNIVOICE_TTS_BACKEND=omnivoice-gguf`.

**GPU offer.** When Compute device is Auto, PyTorch has no GPU, and the bundled
runtime with its Vulkan module finds a GPU, VoiceStudio asks once: *Use your
GPU for faster speech?* The prompt names the GPU, the quant it would run and
the download size, and says that the GGUF model is licensed for noncommercial
use only. **Use GPU** asks you to accept that licence if you have not yet,
switches the TTS engine to OmniVoice GGUF and starts the weight download.
**Not now** keeps your current engine and is remembered. Nothing changes and
nothing downloads until you choose. The same **Use GPU** action stays in
Settings › GPU acceleration for as long as your computer qualifies. The prompt
is not shown if you already picked another engine. The first generate downloads the
weights if they are not installed yet (see [Weights](#weights)).

**Source checkouts:** the repo ships zero-byte placeholders in `bin/` — real
binaries come from CI (`build-omnivoice-tts`) or a local build. The engine detects a placeholder and
reports unavailable with instructions
([#1172](https://github.com/debpalash/VoiceStudio/issues/1172)) instead of
failing at spawn time; build one with
`scripts/build-omnivoice-tts.sh --platform <slug>` or use the default
in-process engine.

**x86-64 CPUs:** Windows, Linux and Intel Mac builds compile every ggml CPU
variant (`GGML_CPU_ALL_VARIANTS`) and pick the best one this processor supports
at startup, so a binary built on a newer machine (for example one with AVX-512)
also runs on older or AVX2-only CPUs ([#2780](https://github.com/debpalash/VoiceStudio/issues/2780)).
A binary built some other way that stops with an *illegal instruction* is
reported as such, and the engine shows *Unavailable* until that binary is
rebuilt with `scripts/build-omnivoice-tts.sh` or replaced.

**Linux ARM64 (Asahi Apple Silicon):** the `linux-aarch64` binary prefers
GGML's Vulkan backend when built on a host with `glslc` and the Khronos
SPIRV headers installed (Arch: `pacman -S shaderc spirv-headers`; Debian:
`apt install glslc libvulkan-dev spirv-headers`), so Apple GPUs accelerate
generation through the open-source Honeykrisp driver. Without those deps the
build falls back to CPU. Expect roughly 2–4x slower generation than macOS
Metal while upstream Mesa and llama.cpp Vulkan optimizations mature; still
well ahead of CPU-only.

## Vulkan acceleration

The Windows x64 and Linux x64 installers ship the runtime with GGML's Vulkan
module (`ggml-vulkan`), built in CI with the Vulkan SDK. Any GPU with a working
Vulkan driver is used: AMD Radeon and Ryzen AI, Intel Arc and Core Ultra, and
NVIDIA.

To size the quant and report the device, VoiceStudio finds the adapter GGML
will open without PyTorch:

1. `vulkaninfo` (installed with the GPU driver on Windows, `vulkan-tools` on
   Linux) gives each GPU's type and its device-local memory heap as a 64-bit
   byte count.
2. Without it, the OS GPU list is used when the Vulkan loader is installed:
   the 64-bit `HardwareInformation.qwMemorySize` registry value on Windows, or
   DRM sysfs on Linux. WMI's `AdapterRAM` is never used; it is 32-bit and
   reports an 8 GB card as 4 GB or less. This list only sizes the quant: it
   does not prove the driver can open the GPU, so without `vulkaninfo` the
   engine keeps the CPU time budget and the GPU offer is not shown.

GGML numbers every discrete and integrated GPU in Vulkan order and opens the
first discrete one, else the first integrated one. With an Intel UHD iGPU at
GPU0 and an Arc card at GPU1 it opens the Arc card (`Vulkan1`), and that is
the card the quant is sized on. In automatic mode VoiceStudio does not set
`GGML_BACKEND`, so GGML makes this choice itself. Settings › GPU acceleration then lists
the engine as using the GPU (Vulkan) with the adapter's name.

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

The pinned runtime selects its own GPU independently of PyTorch. When no
Vulkan GPU can be verified as above (no driver, no Vulkan module, or a CUDA or
Metal route), VoiceStudio reports an unverified native device and uses the
longer CPU generation budget. This does not disable native GPU acceleration. A
Vulkan loader alone does not prove GPU support.

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

## Checking Vulkan on your machine

1. **Driver.** Run `vulkaninfo --summary` (installed with the GPU driver on
   Windows; `vulkan-tools` on Linux). It should list your GPU with
   `PHYSICAL_DEVICE_TYPE_DISCRETE_GPU` or `..._INTEGRATED_GPU`. Only
   `llvmpipe` or an error means no Vulkan driver: install or update the GPU
   driver first.
2. **Runtime.** An installer includes the Vulkan module. On a source checkout,
   download the `omnivoice-tts-<platform>` artifact from a
   `build-omnivoice-tts` CI run and unpack it into `bin/`. Either way,
   `bin/` (or `resources/bin/` in an install) must contain `ggml-vulkan.dll`
   or `libggml-vulkan.so`.
3. **Settings › Models › OmniVoice GGUF** shows *Auto: \<quant\> (Vulkan –
   \<GPU\>)*. The same data is at
   `http://127.0.0.1:3900/engines/omnivoice-gguf/quants` (`hardware` gives the
   adapter, `Vulkan<N>`, dedicated and effective memory).
4. **Settings › GPU acceleration** lists OmniVoice GGUF as using the GPU
   (Vulkan) with your GPU's name. On a computer whose PyTorch has no GPU, the
   *Use your GPU for faster speech?* prompt appears once; **Use GPU** switches
   to this engine.
5. **Generate** one sentence. Press Download first, or let the first
   generation fetch the weights. Watch GPU usage in Task Manager (Windows) or
   `radeontop` / `intel_gpu_top` (Linux).

Expected automatic picks:

| GPU | Device | Effective memory | Quant |
| --- | --- | --- | --- |
| Radeon 890M APU, 32 GB RAM | Vulkan0, shared memory | ~15–16 GB (half the RAM) | Q8_0 (BF16 if `bf16: 1`) |
| Radeon RX 6700 XT 12 GB (`bf16: 0`) | Vulkan0 | ~12 GB | Q8_0 |
| RX 7600 / Arc A750 8 GB | Vulkan0 (or the discrete card's index) | ~8 GB | Q8_0 |
| Intel UHD iGPU + Arc A770 / B580 | Vulkan1 (the Arc card) | 16 / 12 GB | Q8_0 (BF16 if `bf16: 1`) |
| Compute device set to CPU | — | — | Q4_K_M |

If yours differs, report the `vulkaninfo --summary` device list, the
`/engines/omnivoice-gguf/quants` response, and your OS, GPU driver version and
RAM.

## Integrity and self-healing

Before reporting ready, the engine:

- verifies the binary, and every ggml shared library listed next to it,
  against the SHA-256 manifest (`bin/checksums.sha256`), and runs the binary
  with `bin/` as its working directory so no other library can be loaded;
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
- The runtime reads WAV containers only, so a reference clip saved in
  another format (MP3, M4A, Ogg, ...) is converted to a temporary WAV
  before the binary is spawned.
- Same multilingual surface as OmniVoice ([languages.md](../languages.md)).
- Because generation runs in another process, the app's own GPU counters
  don't see its allocations — diagnostics label it accordingly.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_GGUF_GENERATE_TIMEOUT_S` | (generous built-in) | Per-generation timeout for the spawned binary |
| `OMNIVOICE_GGUF_QUANT` | `auto` | Pin a quant (`Q4_K_M`, `Q8_0`, `BF16`, `F32`); overrides Settings |

## Troubleshooting

- "GGUF binary missing": this build doesn't include the runtime (an Intel
  Mac, a store build, Docker or a source checkout; see Setup) — use the default
  engine, reinstall from the GitHub Release download, or build the binary from a
  source checkout.
  The log line `Engine health check failed; engine=omnivoice-gguf` withholds the
  reason by design.
- "GGUF weights are not installed": the runtime binary is there but the pinned
  weight pair is not. Press **Download** in Settings › Models, or generate
  once with network access. Offline, follow the steps in [Weights](#weights);
  the download has to land in the app's own cache.
- The quant picker says *Q4_K_M* on a GPU: no Vulkan GPU was verified. Install
  the GPU's Vulkan driver (and `vulkan-tools` on Linux), then restart the
  backend.
- Checksum mismatch or quarantine messages: follow the printed fix, or
  reinstall.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[performance.md](../performance.md), [disk usage](disk-usage.md).
