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
    <a href="README_CN.md">简体中文</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main" alt="CI" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio" alt="Latest release" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="AGPL-3.0" /></a>
  </p>
</div>

![A tour of the Electron app: voice cloning, voice design, dubbing, and model management](docs/media/electron/voicestudio.gif)

## Your voice. Your workflow.

| Create | Produce | Connect |
| :--- | :--- | :--- |
| Clone a voice or design your own | Dub videos with timed speech | Local API & MCP for agents |
| Dictate with a floating widget | Stories, audiobooks & batch jobs | Optional remote workers |

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

Download from [Releases](https://github.com/debpalash/VoiceStudio/releases/latest), then follow your platform guide:

**[macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md)**

Open **Voice cloning**, choose a voice or add a clean reference recording, enter your text, and generate. Install the required model when prompted. Hardware needs vary by engine; see [performance](docs/performance.md).

### Install with prompt

Copy this prompt into your coding agent to install VoiceStudio and configure it for your device:

```text
Install and configure the VoiceStudio Electron desktop app on this device,
then verify it works. Tauri is archived; do not install or launch it.
Repository: https://github.com/debpalash/VoiceStudio

Read the repository's install guide for my OS, docs/performance.md, and
skills/voicestudio/SKILL.md. Install the voicestudio audio-workflow skill
with `npx skills add debpalash/VoiceStudio` if your agent supports skills;
otherwise follow that SKILL.md directly.

Detect my OS, CPU architecture, GPU, available RAM/VRAM, free disk space,
and any existing VoiceStudio installation, backend, or downloaded models.
Reuse existing data and models. Prefer the latest stable Electron installer
for my OS and architecture; select an asset named VoiceStudio-Electron.
For source setup, follow electron/README.md: bun install, then bun run dev
from the repository root. Let Electron supervise the backend; do not start
a second backend or use legacy tauri scripts.
If migrating from Tauri, follow docs/electron-migration.md and back up first.

Configure local voice cloning using a supported engine and acceleration
that fit this device. Keep working defaults and verify the actual execution
device rather than assuming GPU support. Install required dependencies;
reuse a suitable installed model, or explain the download size and license
and ask before downloading one. Keep cloud services and analytics opt-in.

Start the app, check /health at the configured backend address (default
http://localhost:3900), and discover its API through /openapi.json. Generate
a short test with a bundled or authorized voice and verify the audio file.
Report the installed version, engine, actual device, data location, audio
output path, and how to reopen the app. Complete the setup, not just a plan;
identify any permissions or manual steps you cannot perform.
```

<details>
<summary><strong>Run the Electron preview from source</strong></summary>

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run dev
```

See [Electron setup](electron/README.md) for prerequisites and backend configuration.

Use `bun run smoke-test` to build and launch an isolated packaged Electron app.
Add `-- --install` for the networked managed-runtime installation check.

</details>

> **Electron is the only maintained desktop app.** Version 0.5.3 was the final Tauri release. Existing Tauri users must [install Electron separately](docs/electron-migration.md). Root development, build, test, and release commands target Electron; Tauri source is archived and receives no further updates.

## Documentation

| Need | Start here |
|---|---|
| Setup help | [Troubleshooting](docs/install/troubleshooting.md) · [Model downloads](docs/downloading-models.md) |
| Models & audio quality | [Engine guides](docs/engines/README.md) · [Benchmarks](docs/benchmarks.md) |
| Integrations | [Local API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Examples](examples/README.md) |
| Development | [Contributing](.github/CONTRIBUTING.md) · [Electron](electron/README.md) · [Changelog](CHANGELOG.md) |

Agent skills: `npx skills add debpalash/VoiceStudio` — choose **voicestudio** for audio workflows or **voicestudio-maintainer** for repository maintenance.

## Sponsors

<a href="https://forms.gle/2PYCvd39hbwijzX37"><img src="docs/media/sponsor-slot.svg" alt="Your brand — apply for a featured VoiceStudio sponsor slot" width="640" /></a>

**Become a featured partner.** [Apply for a paid placement](https://forms.gle/2PYCvd39hbwijzX37) · [Email us](mailto:partner@voicestudio.sh)

Support development: [Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [Sponsorship details](SPONSORS.md)

## License & responsible use

[AGPL-3.0](LICENSE). Models have their own licenses; review them before commercial use. Clone voices only with permission. See [license details](LICENSE-NOTICE.md).
