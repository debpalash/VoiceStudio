<div align="center">
  <h1><sub><img src="docs/logo.png" alt="" width="36" height="36" /></sub> VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="Trendshift badge showing where VoiceStudio ranks among trending repositories" width="220" height="48" /></a>
  </p>
  <h2>AI voice studio. On your device.</h2>
  <h3>Clone, design, dub, dictate, transcribe and narrate in 646 languages.</h3>
  <p>Runs on your laptop, desktop, servers, phone, tablets and more</p>
  <p>
    <a href="https://voicestudio.sh/?utm_source=github&utm_medium=readme&utm_campaign=project">Website</a> ·
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest">Download</a> ·
    <a href="#get-started">Get started</a> ·
    <a href="#documentation">Docs</a> ·
    <a href="https://discord.gg/bzQavDfVV9">Discord</a> ·
    <a href="docs/readme/README_CN.md">简体中文</a> · <a href="docs/readme/README_JA.md">日本語</a> · <a href="docs/readme/README_KO.md">한국어</a> · <a href="docs/readme/README_RU.md">Русский</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main&amp;style=flat-square&amp;label=build&amp;labelColor=27272a" alt="CI" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio?style=flat-square&amp;labelColor=27272a&amp;color=52525b" alt="Latest release" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-52525b?style=flat-square&amp;labelColor=27272a" alt="AGPL-3.0" /></a>
    <a href="https://supermetry.com"><img src="https://app.supermetry.com/px/zfjiv7psp4ed.svg?c=VoiceStudio" alt="views" /></a>
  </p>
  <p>
    <sub>Strategic sponsors</sub><br />
    <a href="https://github.com/debpalash/OpenGTM?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/sponsors/opengtm-dark.svg" /><img src="docs/sponsors/opengtm.svg" alt="OpenGTM" height="32" align="middle" /></picture></a>
    <a href="https://github.com/debpalash/Opal?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/sponsors/opal-dark.svg" /><img src="docs/sponsors/opal.svg" alt="Opal" height="32" align="middle" /></picture></a>
    <a href="https://github.com/velixio/supermetry?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/sponsors/supermetry-dark.svg" /><img src="docs/sponsors/supermetry.svg" alt="Supermetry" height="32" align="middle" /></picture></a>
  </p>
</div>

<table>
  <tr><th align="center">Special sponsor · Spicrawl</th></tr>
  <tr><td align="center">
    <a href="https://app.spicrawl.com/signup?utm_source=voicestudio&amp;utm_medium=github_readme&amp;utm_campaign=special_sponsor&amp;utm_content=logo"><img src="docs/sponsors/spicrawl.svg" alt="Spicrawl" width="220" /></a><br />
    <strong>Web scraping for AI agents.</strong><br />
    Turn web pages into clean Markdown or structured JSON with an API, CLI or MCP.<br />
    <a href="https://app.spicrawl.com/signup?utm_source=voicestudio&amp;utm_medium=github_readme&amp;utm_campaign=special_sponsor&amp;utm_content=signup"><strong>Explore Spicrawl</strong></a>
  </td></tr>
</table>

<p align="center">
  <a href="docs/media/electron/voicestudio.mp4">
    <picture>
      <source media="(prefers-reduced-motion: reduce) and (max-width: 768px)" srcset="docs/media/electron/voice-cloning.webp" />
      <source media="(prefers-reduced-motion: reduce)" srcset="docs/media/electron/voice-cloning.png" />
      <img src="docs/media/electron/voicestudio.gif" alt="A tour of the app: voice cloning, voice design, dubbing, and model management" width="100%" />
    </picture>
  </a><br />
  <sub>Demo workspace · Default dark theme · <a href="docs/media/electron/voicestudio.mp4">Watch the HD tour</a></sub>
</p>

<p align="center">
  <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/badge/Download-993f61?style=for-the-badge&amp;logo=github&amp;logoColor=ffffff" alt="Download" height="24" /></a>
  <a href="#get-started"><img src="https://img.shields.io/badge/Get%20started-27272a?style=for-the-badge" alt="Get started" height="24" /></a>
</p>

## What you can do

<table>
  <tr>
    <td width="50%" valign="top">
      <a href="docs/media/electron/voice-cloning.png"><img src="docs/media/electron/voice-cloning.webp" alt="Voice cloning workspace with the bundled demo voice" width="100%" /></a><br />
      <strong><a href="docs/audio-quality.md">Voice cloning</a></strong><br />
      Turn a clean reference recording into speech in a familiar voice.
    </td>
    <td width="50%" valign="top">
      <a href="docs/media/electron/dubbing.png"><img src="docs/media/electron/dubbing.webp" alt="Video dubbing workspace" width="100%" /></a><br />
      <strong><a href="docs/electron-dubbing.md">Video dubbing</a></strong><br />
      Translate and dub videos with speech timed to the original.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <a href="docs/media/electron/voice-design.png"><img src="docs/media/electron/voice-design.webp" alt="Describe a voice in the voice design workspace" width="100%" /></a><br />
      <strong><a href="docs/electron-voice-design.md">Voice design</a></strong><br />
      Describe a voice in words and generate it.
    </td>
    <td width="50%" valign="top">
      <a href="docs/media/electron/models.png"><img src="docs/media/electron/models.webp" alt="Install and manage local speech models" width="100%" /></a><br />
      <strong><a href="docs/downloading-models.md">Local models</a></strong><br />
      Choose your engines and download only the models you need.
    </td>
  </tr>
</table>

<details>
<summary><strong>More screenshots</strong></summary>

<table>
  <tr>
    <td width="50%"><a href="docs/media/electron/overview.png"><img src="docs/media/electron/overview.webp" alt="Workspace overview" width="100%" /></a><br /><strong>Workspace overview</strong></td>
    <td width="50%"><a href="docs/media/electron/model-packs.png"><img src="docs/media/electron/model-packs.webp" alt="Model packs" width="100%" /></a><br /><strong>Model packs</strong></td>
  </tr>
</table>

</details>

- **Dictate & transcribe** — A floating dictation widget and local transcription
- **Narrate & batch** — Stories, audiobooks and queued jobs
- **Connect & automate** — Local API, MCP, reusable recipes and watch folders

Start with **VoiceStudio** (default, powered by k2-fsa/OmniVoice), or pick from dozens of other engines in the Model Manager. See the [features & engine catalog](docs/feature-catalog.md).

## Platforms

| Platform & status | Downloads & acceleration |
| :--- | :--- |
| <img src="docs/assets/platforms/apple.svg" alt="" width="20" height="20" />&nbsp;**macOS** (Apple Silicon)<br /><sub>Stable</sub> | `.dmg` · [guide](docs/install/macos.md)<br /><sub>Metal (MPS)</sub> |
| <img src="docs/assets/platforms/windows.svg" alt="" width="20" height="20" />&nbsp;**Windows 10/11** (x64)<br /><sub>Stable</sub> | `.exe` · [guide](docs/install/windows.md)<br /><sub>NVIDIA CUDA · [AMD ROCm (opt-in)](docs/install/windows-rocm.md) · CPU</sub> |
| <img src="docs/assets/platforms/linux.svg" alt="" width="20" height="20" />&nbsp;**Linux** (x64)<br /><sub>Stable</sub> | `.AppImage` · `.deb` · <a href="https://snapcraft.io/voicestudio"><img src="https://img.shields.io/badge/Snap%20Store-27272a?style=flat-square&amp;logo=snapcraft&amp;logoColor=ffffff" alt="Snap Store" height="20" /></a> · [Nix](docs/install/nix.md) · [guide](docs/install/linux.md)<br /><sub>NVIDIA CUDA · CPU</sub> |
| <img src="docs/assets/platforms/docker.svg" alt="" width="20" height="20" />&nbsp;**Docker**<br /><sub>Stable</sub> | [GHCR](docs/install/docker.md "ghcr.io/debpalash/voicestudio")<br /><sub>NVIDIA CUDA · AMD ROCm</sub> |
| <img src="docs/assets/platforms/android.svg" alt="" width="20" height="20" />&nbsp;**Android** 10+<br /><sub>Preview</sub> | `.apk` · [guide](docs/mobile.md#install)<br /><sub>On-device, offline</sub> |
| <img src="docs/assets/platforms/apple.svg" alt="" width="20" height="20" />&nbsp;**iPhone / iPad** (iOS 17+)<br /><sub>Preview</sub> | `.ipa` (sideload) · [guide](docs/mobile.md#install)<br /><sub>On-device, offline</sub> |
| <img src="docs/assets/platforms/windows.svg" alt="" width="20" height="20" />&nbsp;**Windows on ARM**<br /><sub>Experimental</sub> | `.exe` · [guide](docs/install/windows.md)<br /><sub>CPU (x64 backend under emulation)</sub> |
| <img src="docs/assets/platforms/apple.svg" alt="" width="20" height="20" />&nbsp;**Intel Mac**<br /><sub>Limited</sub> | `.dmg` · [why](docs/install/macos.md)<br /><sub>App UI only, connects to a remote backend</sub> |

Desktop and phone installers are on the [latest release](https://github.com/debpalash/VoiceStudio/releases/latest); Docker images come from GHCR. No GPU? The app is fully usable on the CPU, just slower. Native GGUF engines can also use Vulkan on AMD, Intel and NVIDIA GPUs; OmniVoice GGUF picks its GPU and model size automatically on Windows and Linux ([OmniVoice](docs/engines/omnivoice-gguf.md#vulkan-acceleration), [CosyVoice](docs/engines/cosyvoice-gguf.md)). Speed by engine and hardware: [performance](docs/performance.md).

## Get started

**macOS / Linux**

```sh
curl -fsSL https://voicestudio.sh/install | sh
```

**Windows** (PowerShell)

```powershell
irm https://voicestudio.sh/install.ps1 | iex
```

Or download an installer from the table above. Then open **Voice cloning**, pick a voice or add a clean reference recording, type your text and generate. The app asks before it downloads any model.

> [!NOTE]
> On macOS, "VoiceStudio is damaged and can't be opened" means Gatekeeper blocked the app, not that the download is broken. See the [Gatekeeper fix](docs/install/macos.md#gatekeeper-quarantine).

<details>
<summary><strong>More install options</strong>: a specific version, current main, uninstall, from source, with a coding agent</summary>

```sh
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z   # a specific release
curl -fsSL https://voicestudio.sh/install | sh -s -- --main            # build current main
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall       # remove the app, keep your data
```

If `voicestudio.sh` is unreachable, run the script straight from the repository:
`curl -fsSL https://raw.githubusercontent.com/debpalash/VoiceStudio/main/scripts/install.sh | sh`.
Prerequisites and behavior: [installer guide](docs/install/script.md).

**From source**

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun run dev
```

`bun run dev` installs the JavaScript dependencies and, when using a supported
local backend, the Python dependencies before launching Electron. Run
`bun run setup:api` directly when you only need to rebuild the Python environment.

See [Electron setup](electron/README.md).

**With a coding agent** (Claude Code, Codex, Cursor, …), paste:

```text
Install the VoiceStudio app on this device and verify it works, following
https://github.com/debpalash/VoiceStudio/blob/main/docs/install/agent.md
```

Upgrading from 0.5.3 or older (the Tauri app)? [Install the new app separately](docs/electron-migration.md).

</details>

## Documentation

| Need | Start here |
|---|---|
| Setup help | [Troubleshooting](docs/install/troubleshooting.md) · [Model downloads](docs/downloading-models.md) |
| Guides | [Multi-speaker dubbing](docs/dubbing/multi-speaker-guide.md) · [Expressive speech](docs/expressive-speech.md) · [Exporting dubs](docs/dubbing/export.md) |
| Models & quality | [Engine guides](docs/engines/README.md) · [whisper.cpp ASR](docs/engines/whisper-cpp.md) · [Benchmarks](docs/benchmarks.md) |
| Integrations | [Local API](docs/speech-platform.md) · [MCP](docs/mcp.md) · [Examples](examples/README.md) |
| Phone apps | [iOS and Android](docs/mobile.md) |
| Development | [Contributing](.github/CONTRIBUTING.md) ([sign the CLA](.github/CONTRIBUTING.md#contribution-licensing) before your first PR) · [Changelog](CHANGELOG.md) |

Agent skills: `npx skills add debpalash/VoiceStudio` (**voicestudio** for audio work, **voicestudio-maintainer** for the repo).

## Sponsors

VoiceStudio is free and has no paid cloud behind it. Sponsors keep it shipping: they fund maintainer time, test hardware, code-signing and release infrastructure.

<p>
    <a href="https://github.com/debpalash/OpenGTM?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/sponsors/opengtm-dark.svg" /><img src="docs/sponsors/opengtm.svg" alt="OpenGTM" height="48" align="middle" /></picture></a>
    <a href="https://github.com/debpalash/Opal?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/sponsors/opal-dark.svg" /><img src="docs/sponsors/opal.svg" alt="Opal" height="48" align="middle" /></picture></a>
    <a href="https://github.com/velixio/supermetry?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/sponsors/supermetry-dark.svg" /><img src="docs/sponsors/supermetry.svg" alt="Supermetry" height="48" align="middle" /></picture></a>
</p>

For sponsorship inquiries, contact [sponsor@voicestudio.sh](mailto:sponsor@voicestudio.sh).

<a href="https://github.com/sponsors/debpalash"><img src="https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa?logo=githubsponsors&logoColor=white" alt="Sponsor on GitHub Sponsors" /></a>
[Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [Tiers and benefits](SPONSORS.md) · [Get your logo listed](https://github.com/debpalash/VoiceStudio/issues/new?template=sponsor.yml)

## Other projects

More open-source tools from the people building VoiceStudio.

- **[OpenGTM](https://opengtm.palash.dev/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=related-projects)** — Find and enrich companies and people in a self-hosted go-to-market workspace. [Source](https://github.com/debpalash/OpenGTM).
- **[Opal](https://opal.palash.dev/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=related-projects)** — Watch, stream and browse your media in one native, open-source player. [Source](https://github.com/debpalash/Opal).
- **[Supermetry](https://supermetry.com/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=related-projects)** — Understand your websites and products with self-hosted, cookie-free analytics. [Source](https://github.com/velixio/supermetry).

More projects: [debpalash](https://github.com/debpalash?tab=repositories) · [velixio](https://github.com/velixio?tab=repositories)

## Team

### Core team

<table>
<tr>
<td align="center" width="200"><a href="https://github.com/debpalash"><img src="https://github.com/debpalash.png?size=160" width="80" height="80" alt="Palash Debnath"></a><br><b>Palash Debnath</b><br><sub>Creator and Core Team</sub><br><sub><a href="https://palash.dev">palash.dev</a> · <a href="https://github.com/debpalash">GitHub</a> · <a href="https://x.com/idebpalash">X</a></sub></td>
<td align="center" width="200"><a href="https://github.com/velixio"><img src="https://github.com/velixio.png?size=160" width="80" height="80" alt="Shimul Debnath"></a><br><b>Shimul Debnath</b><br><sub>Core Team</sub><br><sub><a href="https://shimul.me">shimul.me</a> · <a href="https://github.com/velixio">GitHub</a> · <a href="https://x.com/shimuldn">X</a></sub></td>
</tr>
</table>

### Contributors

<a href="https://github.com/kevin9327"><img src="https://github.com/kevin9327.png?size=96" width="44" height="44" alt="@kevin9327" title="@kevin9327"></a> <a href="https://github.com/jaketame"><img src="https://github.com/jaketame.png?size=96" width="44" height="44" alt="@jaketame" title="@jaketame"></a> <a href="https://github.com/psiberfunk"><img src="https://github.com/psiberfunk.png?size=96" width="44" height="44" alt="@psiberfunk" title="@psiberfunk"></a> <a href="https://github.com/rudycelekli"><img src="https://github.com/rudycelekli.png?size=96" width="44" height="44" alt="@rudycelekli" title="@rudycelekli"></a> <a href="https://github.com/paoloantinori"><img src="https://github.com/paoloantinori.png?size=96" width="44" height="44" alt="@paoloantinori" title="@paoloantinori"></a> <a href="https://github.com/Chang-Jin-Lee"><img src="https://github.com/Chang-Jin-Lee.png?size=96" width="44" height="44" alt="@Chang-Jin-Lee" title="@Chang-Jin-Lee"></a> <a href="https://github.com/swadhinbiswas"><img src="https://github.com/swadhinbiswas.png?size=96" width="44" height="44" alt="@swadhinbiswas" title="@swadhinbiswas"></a> <a href="https://github.com/BichengWang"><img src="https://github.com/BichengWang.png?size=96" width="44" height="44" alt="@BichengWang" title="@BichengWang"></a> <a href="https://github.com/nirmalchatur"><img src="https://github.com/nirmalchatur.png?size=96" width="44" height="44" alt="@nirmalchatur" title="@nirmalchatur"></a> <a href="https://github.com/Shivendra-Coherent"><img src="https://github.com/Shivendra-Coherent.png?size=96" width="44" height="44" alt="@Shivendra-Coherent" title="@Shivendra-Coherent"></a> <a href="https://github.com/marreiradigital"><img src="https://github.com/marreiradigital.png?size=96" width="44" height="44" alt="@marreiradigital" title="@marreiradigital"></a> <a href="https://github.com/nvtoan0201-swe"><img src="https://github.com/nvtoan0201-swe.png?size=96" width="44" height="44" alt="@nvtoan0201-swe" title="@nvtoan0201-swe"></a>

Thank you to everyone who sends code, reports, translations and ideas. [All contributors](https://github.com/debpalash/VoiceStudio/graphs/contributors) · [Contribute](.github/CONTRIBUTING.md)

## License & responsible use

[AGPL-3.0](LICENSE) covers the app, including commercial use under its terms. Model weights, voices and generated output have their own licences, which the Model Manager shows for each model ([inventory](docs/licensing/model-inventory.md)). Clone a voice only with its owner's permission. Details: [LICENSE-NOTICE.md](LICENSE-NOTICE.md).
