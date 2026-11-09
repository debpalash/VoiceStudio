# VoiceStudio — audio.cpp Engines (Breeze-TTS-2, dots.tts)

[audio.cpp](https://github.com/0xShug0/audio.cpp) is a pure-C++ ggml audio
inference framework with prebuilt binaries for Windows, macOS, and Linux and
no Python dependency. VoiceStudio discovers the compute providers compiled
into the installed binary and selects the best available device. VoiceStudio drives its
`audiocpp_server` over loopback HTTP. Two engines share one runtime:

- **`audiocpp`** — the **`breeze_tts`** family: **Breeze-TTS-2** (BreezeBlue,
  3B params, English + Chinese, voice clone + voice design + voice direction,
  24 kHz). Research/non-commercial weights.
- **`audiocpp-dots`** — the **`dots_tts`** family: **dots.tts SOAR**
  (multilingual zero-shot voice cloning, Apache-2.0). See [dots.tts](#dotstts).

> **Opt-in, and never a default.** Select `audiocpp` or `audiocpp-dots`
> explicitly in **Model Catalogue → Engines** (or `OMNIVOICE_TTS_BACKEND`).

## License — read before enabling

- **audio.cpp code:** Apache-2.0.
- **Breeze-TTS-2 weights** (upstream `BreezeBlue/Breeze-TTS-2` and the
  `audio-cpp/audio.cpp-gguf` GGUF repack): **research and non-commercial
  use only** under the [BreezeBlue Research and Non-Commercial License](
  https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/main/LICENSE).
  Self-hosted outputs inherit the restriction; a BreezeBlue paid
  subscription covers hosted-platform outputs only, not this engine.

## Platform support

| Host | Binary | Compute |
|---|---|---|
| Windows x64 | CPU, Vulkan, CUDA 12.4/13.3 prebuilts | CPU, Vulkan, CUDA |
| Linux x64 | CPU and Vulkan prebuilts | CPU, Vulkan; CUDA/ROCm from a self-build |
| macOS arm64 | upstream Metal prebuilt | Metal + CPU |
| macOS x64 | upstream `metal` archive (Metal disabled by upstream) | CPU |
| Linux aarch64 | none upstream | unavailable in v1 |

VoiceStudio runs `audiocpp_server --list-devices` once per installed binary,
prefers CUDA, ROCm, Metal, then a discrete Vulkan GPU, and keeps CPU as a safe
fallback. Device numbers are local to each backend registry. The Q8_0 GGUF is
approximately 4.73 GiB, plus runtime memory.

Allow about 6 GB of dedicated VRAM for CUDA, HIP, or a discrete GPU exposed
through Vulkan. Metal and Vulkan integrated GPUs use unified-memory handling
instead of that dedicated-VRAM floor.

## Install

On a supported desktop, install the model from **Settings → Models**, then use
**Install** on the audio.cpp engine row (the TTS rows and the Sortformer
diarisation row install the same shared runtime). VoiceStudio fetches
the pinned archive, verifies its published size and SHA-256, rejects unsafe
archive paths, probes its device list, and installs it under the update-surviving
app-data engine directory. The action is explicit; generation never downloads
or updates executable code.

For a user-managed runtime:

1. Download the v0.7.4 prebuilt for your platform from
   [audio.cpp releases](https://github.com/0xShug0/audio.cpp/releases/tag/v0.7.4)
   and extract it. Use the Vulkan archive on Windows or Linux for broad GPU
   support, the CPU archive when Vulkan is unavailable, or the matching CUDA
   archive on Windows for NVIDIA. A Windows CUDA install needs both the
   `bin-…-cuda…` and matching `cudart-…-cuda…` archives extracted into the
   same directory, as required by upstream. Linux archives do not preserve the
   executable bit, so run `chmod +x audiocpp_server` after extracting one.

   Verify the archive before extracting it. The pinned SHA-256 checksums are:

   | Archive | SHA-256 |
   |---|---|
   | `audio-v0.7.4-bin-windows-x64-cpu-portable.zip` | `d241c56ba78fd3c1b28bf289792fb8ec258d36586b4e0c8d667080ec248c0d2f` |
   | `audio-v0.7.4-bin-windows-x64-vulkan.zip` | `057332f9e3fb37706a8ecb5075ac1797efcd85fdccd739f7b65761a5920f2828` |
   | `audio-v0.7.4-bin-windows-x64-cuda12.4.zip` | `83fdd5b6e7bd4362604c10cc88d7d3564ef82030dc1d21c693a62cdcbe2e5e38` |
   | `audio-v0.7.4-cudart-windows-x64-cuda12.4.zip` | `88d8943a2a8011f02c2a4efa7dbbe258608362615cce51e7f0e0e3a0c62f5a43` |
   | `audio-v0.7.4-bin-windows-x64-cuda13.3.zip` | `af56012969bcb68f54e6ea14a123e5c6ecd62830c1b2816080eb7f99d985a779` |
   | `audio-v0.7.4-cudart-windows-x64-cuda13.3.zip` | `c20793d8cc9b7c66ab28ab335aa908c726f2df15832330ddcfdbc837c7670145` |
   | `audio-v0.7.4-bin-ubuntu-x64-cpu.tar.gz` | `638e6114550c5ea02b96907de400379c8f31bd13325525083f98d158027acc40` |
   | `audio-v0.7.4-bin-ubuntu-x64-vulkan.tar.gz` | `e0ef3123a9f94e130ad463db0db5a69b65485ef8db1b46edead00c03a86fa787` |
   | `audio-v0.7.4-bin-macos-arm64-metal.tar.gz` | `639926715b1cb537f82aa31656aabbae5d9a85ac36568c402026968f3072e2b3` |
   | `audio-v0.7.4-bin-macos-x64-metal.tar.gz` | `bdb797d54dcf8416bd5ac0fac282ce5500dd08843f8f22e20e9fc378ebc24c1f` |

   Run `sha256sum <archive>` on Linux, `shasum -a 256 <archive>` on macOS,
   or `Get-FileHash <archive> -Algorithm SHA256` in PowerShell and compare the
   complete result with the table.
2. Set `OMNIVOICE_AUDIOCPP_BIN` to the `audiocpp_server` binary
   (`audiocpp_server.exe` on Windows):

   ```bash
   # macOS / Linux
   echo 'export OMNIVOICE_AUDIOCPP_BIN=$HOME/apps/audio.cpp/audiocpp_server' >> ~/.zshrc
   source ~/.zshrc
   ```

   Alternatively set `OMNIVOICE_AUDIOCPP_DIR` to the directory containing it.
3. Restart VoiceStudio, open **Model Catalogue (TTS tab → the engine's Weights)**, find
   **Breeze-TTS-2 Q8_0 for audio.cpp**, review its research/non-commercial
   license note, and click **Install**. Generation never starts this ~4.73 GiB
   download automatically.
4. Pick `audiocpp` in **Model Catalogue** (TTS tab → **Use**). The server starts
   lazily on first generate (`server.json` + `server.log` live under the app
   data `audiocpp/` directory).

## dots.tts

`audiocpp-dots` runs dots.tts SOAR on the same runtime, so it works on Windows
as well as macOS and Linux — unlike the Python [dots.tts engine](dots-tts.md),
which has no Windows install path ([#2665](https://github.com/debpalash/VoiceStudio/issues/2665)).

1. Install the runtime as above.
2. In **Model Catalogue (TTS tab → audio.cpp · dots.tts → Weights)**, install
   **audio.cpp dots.tts SOAR (Q8_0)** (~2.96 GB, from the same pinned
   `audio-cpp/audio.cpp-gguf` revision as Breeze-TTS-2). It is a separate
   package: installing or uninstalling it never touches Breeze-TTS-2 or
   Sortformer, and the reverse.
3. Pick `audiocpp-dots` and clone a voice: a reference clip plus its
   transcript (the transcript is optional but improves the match). It
   reads the language from the text. There is no voice design mode, and it
   uses its own tuned guidance (1.2) rather than the shared guidance slider.

It runs its own server on port `17861` with `server.json` and `server.log`
under the app data `audiocpp-dots/` directory.

## Voice modes

Breeze-TTS-2 (`audiocpp`). All three go through the one speech endpoint — reference presence selects:

- **Clone:** `ref_audio` + `ref_text` (exact transcript, as upstream).
- **Direction:** `ref_audio` + `ref_text` + `instruct`
  (e.g. "Speak slowly with a restrained, serious tone").
- **Design:** `description` (or `instruct`) with no `ref_audio`
  (e.g. "A warm, thoughtful young woman…"). Upstream strengthens
  instruction-following with `guidance_scale` ≈ 4.

## Optional env knobs

| Variable | Default | Purpose |
|----------|---------|---------|
| `OMNIVOICE_AUDIOCPP_BIN` | — | Absolute path to `audiocpp_server`. |
| `OMNIVOICE_AUDIOCPP_DIR` | — | Directory containing `audiocpp_server`. |
| `OMNIVOICE_AUDIOCPP_MODEL` | Model Catalogue cache | GGUF file or directory override. |
| `OMNIVOICE_AUDIOCPP_PACKAGE` | `breeze-tts-2-q8_0.gguf` | Package filename (`…-bf16.gguf` for full precision). |
| `OMNIVOICE_AUDIOCPP_PORT` | `17860` | Loopback port. |
| `OMNIVOICE_AUDIOCPP_BACKEND` | Settings, then auto | Exact runtime: `cuda`, `hip`/`rocm`, `vulkan`, `metal`, or `cpu`. |
| `OMNIVOICE_AUDIOCPP_DEVICE` | best device | Backend-local non-negative device index; requires `OMNIVOICE_AUDIOCPP_BACKEND`. |
| `OMNIVOICE_AUDIOCPP_DOTS_MODEL` | Model Catalogue cache | dots.tts GGUF file or directory override. |
| `OMNIVOICE_AUDIOCPP_DOTS_PACKAGE` | `dots-tts-soar-q8_0.gguf` | dots.tts package filename (`dots-tts-soar-bf16.gguf` for full precision). |
| `OMNIVOICE_AUDIOCPP_DOTS_PORT` | `17861` | dots.tts loopback port. |

`OMNIVOICE_AUDIOCPP_BIN`/`_DIR`/`_BACKEND`/`_DEVICE` apply to both engines; the
`_MODEL`/`_PACKAGE`/`_PORT` variables without `DOTS` apply to Breeze-TTS-2 only.

The audio.cpp overrides take precedence over the global Settings compute
choice. A global CUDA/ROCm choice can match an NVIDIA/AMD GPU exposed through
Vulkan. An unavailable explicit audio.cpp override is an error; an unavailable
global preference falls back to CPU and is shown as a routing fallback.

## Common errors

### `audiocpp_server not found ...`

The binary isn't installed. Follow **Install** — the message carries the
exact release URL and SHA for your platform.

### `audiocpp_server exited during startup ...`

The managed loopback port may be taken. Check `server.log` next to
`server.json` in the app data `audiocpp/` directory, or set a different
`OMNIVOICE_AUDIOCPP_PORT` and restart VoiceStudio.

### `Breeze-TTS-2 ... not installed`, `dots.tts ... not installed` or `package ... not completely installed`

Install the model from **Model Catalogue (TTS tab → the engine's Weights)**. If an interrupted install
left it incomplete, use **Reinstall** there. If the error persists after a
complete reinstall, file an issue with the package listing.

---

audio.cpp runs as a managed native server (no Python venv, no
`transformers` conflict). Only the downloaded GGUF counts toward
[sidecar disk usage](disk-usage.md).

The catalogue checks each package (Breeze-TTS-2 + Sortformer, dots.tts) inside the shared GGUF repository on its own: one package never makes another appear installed, and uninstalling one removes only its own files. Required weight files must also satisfy the normal weight-size floor; empty or truncated placeholders remain incomplete. Broader native model discovery and execution are still pending integration.
