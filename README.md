<div align="center">
  <img src="docs/logo.png" alt="VoiceStudio" width="88" />
  <h1>VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="VoiceStudio ranking on Trendshift" width="220" height="48" /></a>
  </p>
  <p><strong>Open-source voice cloning, voice design, video dubbing, dictation, transcription & audiobook creation in 646 languages.</strong></p>
  <p>
    <a href="https://voicestudio.sh/?utm_source=github&utm_medium=readme&utm_campaign=project">Website</a> ·
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest">Download</a> ·
    <a href="#get-started">Get started</a> ·
    <a href="#documentation">Docs</a> ·
    <a href="https://discord.gg/bzQavDfVV9">Discord</a> ·
    <a href="README_CN.md">简体中文</a> ·
    <a href="README_JA.md">日本語</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main" alt="CI" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio" alt="Latest release" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
    <a href="https://supermetry.com"><img src="https://app.supermetry.com/px/zfjiv7psp4ed.svg?c=VoiceStudio&style=stats" alt="views" /></a>
  </p>
</div>

![A tour of the Electron app: voice cloning, voice design, dubbing, and model management](docs/media/electron/voicestudio.gif)

## Your voice. Your workflow.

| Create | Produce | Connect |
| :--- | :--- | :--- |
| Clone a voice or design your own | Dub videos with timed speech | Local API & MCP for agents |
| Dictate with a floating widget | Stories, audiobooks & batch jobs | Reusable recipes & watch folders |

Start with **VoiceStudio** (default, powered by k2-fsa/OmniVoice), or choose another engine. [Features & engine catalog](docs/feature-catalog.md).

Local workflows run on your hardware. Remote services are optional; usage analytics requires consent.

<details>
<summary><strong>Explore the workspaces</strong> · Clone, dub, design & models</summary>

<table>
  <tr>
    <td><img src="docs/media/electron/voice-cloning.png" alt="Electron voice cloning workspace with the bundled demo voice" width="100%" /></td>
    <td><img src="docs/media/electron/dubbing.png" alt="Electron video dubbing workspace" width="100%" /></td>
  </tr>
  <tr><td align="center">Voice cloning</td><td align="center">Video dubbing</td></tr>
  <tr>
    <td><img src="docs/media/electron/voice-design.png" alt="Describe a voice in the Electron voice design workspace" width="100%" /></td>
    <td><img src="docs/media/electron/models.png" alt="Install and manage local speech models" width="100%" /></td>
  </tr>
  <tr><td align="center">Voice design</td><td align="center">Local models</td></tr>
</table>

<img width="2628" height="1950" alt="VoiceStudio desktop workspace" src="https://github.com/user-attachments/assets/b474497d-a453-49a3-a2dd-f023ec6b7659" />

</details>

## Get started

### One-command install (macOS / Linux)

```sh
# Latest Electron release
curl -fsSL https://voicestudio.sh/install | sh

# A specific published Electron release (replace X.Y.Z)
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z

# Build current main and install the desktop app
curl -fsSL https://voicestudio.sh/install | sh -s -- --main

# Uninstall the app, keeping your data
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall
```

On Windows, run `irm https://voicestudio.sh/install | iex` in PowerShell.

If `voicestudio.sh/install` is unreachable or returns 404, run the same script
straight from the repository, or clone it and run `scripts/install.sh`:

```bash
curl -fsSL https://raw.githubusercontent.com/debpalash/VoiceStudio/main/scripts/install.sh | sh
```

Release downloads require curl and a SHA-256 tool. `--main` requires Git,
Node.js 22+, Bun, Rust/Cargo, and platform build tools; see
[installer prerequisites and behavior](docs/install/script.md).
The installer preserves your settings, projects, and models. Older versions
must contain Electron packages; it never falls back to archived Tauri builds.

Or download from [Releases](https://github.com/debpalash/VoiceStudio/releases/latest) (macOS `.dmg`, Windows `.exe`, Linux `.AppImage` or `.deb`) and follow your platform guide:

**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**

| Hardware | Support |
|---|---|
| NVIDIA GPU (Windows / Linux) | CUDA acceleration |
| Apple Silicon | Metal (MPS) acceleration |
| No dedicated GPU (Intel/AMD integrated graphics, older PCs) | Fully usable on the CPU, slower; setup installs the small CPU build of PyTorch (about 5 GB free disk) |
| Windows on ARM (Snapdragon X etc.) | **Experimental**, validation pending: native ARM64 app, x64 Python backend under emulation, CPU only |
| Intel Mac | App UI only; connect to a remote backend ([why](docs/install/macos.md)) |

Open **Voice cloning**, choose a voice or add a clean reference recording, enter your text, and generate. Install the required model when prompted. Hardware needs vary by engine; see [performance](docs/performance.md).

### Install with prompt

Paste into your coding agent (Claude Code, Codex, Cursor, …):

```text
Install the VoiceStudio Electron app on this device and verify it works, following
https://github.com/debpalash/VoiceStudio/blob/main/docs/install/agent.md
```

The [agent guide](docs/install/agent.md) covers hardware detection, reusing existing
data, asking before model downloads, and a test generation. Agents that support skills
can also run `npx skills add debpalash/VoiceStudio`.

<details>
<summary><strong>Run the Electron preview from source</strong></summary>

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api  # prepare Python dependencies before starting Electron
bun run dev
```

See [Electron setup](electron/README.md) for prerequisites and backend configuration.

Use `bun run smoke-test` to build and launch an isolated packaged Electron app.
Add `-- --install` for the networked managed-runtime installation check.

</details>

> **Electron is the only desktop app and web UI.** Version 0.5.3 was the final Tauri release. Existing Tauri users must [install Electron separately](docs/electron-migration.md). The retired Tauri shell and legacy UI entry points have been removed.

## Documentation

| Need | Start here |
|---|---|
| Setup help | [Troubleshooting](docs/install/troubleshooting.md) · [Model downloads](docs/downloading-models.md) |
| Models & audio quality | [Engine guides](docs/engines/README.md) · [Benchmarks](docs/benchmarks.md) |
| Integrations | [Local API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Examples](examples/README.md) |
| Phone apps (preview, run offline) | [iOS and Android](docs/mobile.md) |
| Development | [Contributing](.github/CONTRIBUTING.md) ([CLA signing guidance](.github/CONTRIBUTING.md#contribution-licensing) for your first PR) · [Electron](electron/README.md) · [Changelog](CHANGELOG.md) |

Agent skills: `npx skills add debpalash/VoiceStudio` — choose **voicestudio** for audio workflows or **voicestudio-maintainer** for repository maintenance.

## Sponsors

Sponsorship funds maintainer time, development tools, release infrastructure and test hardware; it buys recognition, not features.

[GitHub Sponsors](https://github.com/sponsors/debpalash) · [Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [Tiers and recognition](SPONSORS.md) · [Ask for recognition](https://github.com/debpalash/VoiceStudio/issues/new?template=sponsor.yml)

## License & responsible use

[AGPL-3.0](LICENSE) covers the application, including commercial use under its conditions. Model weights, tokenizers, voices and generated-output conditions are separate; a paid app licence does not grant those rights. Clone voices only with permission. Model Manager now shows versioned model-source notices, evidence links and unresolved review gaps from the [shared inventory](docs/licensing/model-inventory.md). Its optional [reviewed-file preview](docs/licensing/model-review-workflow.md) does not enforce ordinary downloads or certify commercial permission. See [license details](LICENSE-NOTICE.md).


### Optional whisper.cpp speech recognition

For local transcription and translation, install the upstream
[whisper.cpp CLI](https://github.com/ggml-org/whisper.cpp) and a compatible
Whisper GGML model (for example `ggml-base.bin`). Binaries and models are
user-managed; VoiceStudio does not download them automatically or offer them
through Model Catalogue. Use a Vulkan-enabled CLI build for AMD GPU acceleration,
a CUDA-enabled build for NVIDIA, or a Metal-enabled build on macOS. A Vulkan
loader alone does not establish GPU support in the installed CLI.

Set these environment variables before starting VoiceStudio (all desktop OSes):

- `OMNIVOICE_ASR_BACKEND=whisper-cpp` selects the engine explicitly.
- `OMNIVOICE_WHISPER_CPP_BIN` points to `whisper-cli` (`whisper-cli.exe` on Windows).
- `OMNIVOICE_WHISPER_CPP_MODEL` points to your downloaded model file.
- `OMNIVOICE_WHISPER_CPP_DEVICE` optionally selects the native GPU index.
- `OMNIVOICE_WHISPER_CPP_BACKEND=cpu` disables native GPU acceleration.

Alternatively, put the CLI in `backend/engines/whisper_cpp/bin/` or on `PATH`;
`OMNIVOICE_WHISPER_CPP_DIR` selects its directory. On Linux and macOS, the CLI
must have execute permission (`chmod +x /path/to/whisper-cli`). Legacy `main` executables are
accepted only in an explicitly selected or package-local directory. Models are
also found in `backend/engines/whisper_cpp/models/`, `~/.cache/whisper/`, or the
existing Hugging Face cache for `ggerganov/whisper.cpp`. Global CPU selection
always disables GPU use. Automatic ASR selection remains unchanged; diagnostics
use conservative CPU estimates because the CLI's GPU providers are not verified.
Older CLI builds without full JSON output can transcribe without word timings.
If the CLI omits detected language metadata or returns malformed language
metadata, it stays unknown: select a source
language in interactive dubbing before translating; batch dubbing requires ASR
language metadata.
In Dubbing, the source language you choose applies everywhere the source text
is read: the translation request, the Argos language packs checked and installed
for the job, and the glossary terms extracted from it. Auto-detection only fills
in while no explicit choice is made, and when neither the choice nor the
detection names a language the source stays unknown instead of defaulting to
English.
Translation requires a multilingual model; English-only `.en`, Turbo, and
Distil checkpoints are rejected for translation. That decision follows the
checkpoint's own file name, so a model cached in the Hugging Face hub stays
recognisable: the cache stores each model as a symlink into a
content-addressed blob, and the cached entry's name (not the blob's hash)
decides what the checkpoint can do. The licence check follows the file
rather than the name: a selected model keeps the terms of the repository
that physically holds it, even when a cache entry points somewhere else.
Models resolved from a Hugging Face cache retain their source repository
licence check. Other custom local files have no assumed repository identity;
their terms remain your responsibility.
Audio conversion permits local file and pipe inputs only. Remote URL entries
in uploaded playlists are rejected.
