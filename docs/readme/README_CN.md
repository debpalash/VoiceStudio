<div align="center">
  <h1><sub><img src="../../docs/logo.png" alt="" width="36" height="36" /></sub> VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="VoiceStudio 在 Trendshift 上的热度排名徽章" width="220" height="48" /></a>
  </p>
  <h2>AI 语音工作室，就在你的设备上。</h2>
  <h3>支持 646 种语言的声音克隆、声音设计、配音、听写、转录与有声朗读。</h3>
  <p>可在你的笔记本电脑、台式机、服务器、手机、平板电脑等设备上运行</p>
  <p>
    <a href="https://voicestudio.sh/?utm_source=github&utm_medium=readme&utm_campaign=project">官网</a> ·
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest">下载</a> ·
    <a href="#开始使用">快速上手</a> ·
    <a href="#文档">文档</a> ·
    <a href="https://discord.gg/bzQavDfVV9">Discord</a> ·
    <a href="../../README.md">English</a> · <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <a href="README_RU.md">Русский</a>
  </p>
  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main&amp;style=flat-square&amp;label=build&amp;labelColor=27272a" alt="CI 构建状态" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio?style=flat-square&amp;labelColor=27272a&amp;color=52525b" alt="最新版本" /></a>
    <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-52525b?style=flat-square&amp;labelColor=27272a" alt="AGPL-3.0 许可证" /></a>
    <a href="https://supermetry.com"><img src="https://app.supermetry.com/px/zfjiv7psp4ed.svg?c=VoiceStudio" alt="访问量" /></a>
  </p>
  <p>
    <sub>战略赞助商</sub><br />
    <a href="https://github.com/debpalash/OpenGTM?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="../../docs/sponsors/opengtm-dark.svg" /><img src="../../docs/sponsors/opengtm.svg" alt="OpenGTM" height="32" align="middle" /></picture></a>
    <a href="https://github.com/debpalash/Opal?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="../../docs/sponsors/opal-dark.svg" /><img src="../../docs/sponsors/opal.svg" alt="Opal" height="32" align="middle" /></picture></a>
    <a href="https://github.com/velixio/supermetry?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="../../docs/sponsors/supermetry-dark.svg" /><img src="../../docs/sponsors/supermetry.svg" alt="Supermetry" height="32" align="middle" /></picture></a>
  </p>
</div>

<table>
  <tr><th align="center">特别赞助商 · Spicrawl</th></tr>
  <tr><td align="center">
    <a href="https://app.spicrawl.com/signup?utm_source=voicestudio&amp;utm_medium=github_readme&amp;utm_campaign=special_sponsor&amp;utm_content=logo"><img src="../../docs/sponsors/spicrawl.svg" alt="Spicrawl" width="220" /></a><br />
    <strong>面向 AI 智能体的网页抓取服务。</strong><br />
    通过 API、CLI 或 MCP，将网页转换为整洁的 Markdown 或结构化 JSON。<br />
    <a href="https://app.spicrawl.com/signup?utm_source=voicestudio&amp;utm_medium=github_readme&amp;utm_campaign=special_sponsor&amp;utm_content=signup"><strong>探索 Spicrawl</strong></a>
  </td></tr>
</table>

<p align="center">
  <a href="../../docs/media/electron/voicestudio.mp4">
    <picture>
      <source media="(prefers-reduced-motion: reduce) and (max-width: 768px)" srcset="../../docs/media/electron/voice-cloning.webp" />
      <source media="(prefers-reduced-motion: reduce)" srcset="../../docs/media/electron/voice-cloning.png" />
      <img src="../../docs/media/electron/voicestudio.gif" alt="应用导览：声音克隆、声音设计、配音和模型管理" width="100%" />
    </picture>
  </a><br />
  <sub>演示工作区 · 默认深色主题 · <a href="../../docs/media/electron/voicestudio.mp4">观看高清演示</a></sub>
</p>

<p align="center">
  <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/badge/%E4%B8%8B%E8%BD%BD-993f61?style=for-the-badge&amp;logo=github&amp;logoColor=ffffff" alt="下载" height="24" /></a>
  <a href="#开始使用"><img src="https://img.shields.io/badge/%E5%BF%AB%E9%80%9F%E4%B8%8A%E6%89%8B-27272a?style=for-the-badge" alt="快速上手" height="24" /></a>
</p>

## 你可以做什么

<table>
  <tr>
    <td width="50%" valign="top">
      <a href="../../docs/media/electron/voice-cloning.png"><img src="../../docs/media/electron/voice-cloning.webp" alt="使用内置演示声音的声音克隆工作区" width="100%" /></a><br />
      <strong><a href="../../docs/audio-quality.md">声音克隆</a></strong><br />
      用一段清晰的参考录音，生成熟悉声音的语音。
    </td>
    <td width="50%" valign="top">
      <a href="../../docs/media/electron/dubbing.png"><img src="../../docs/media/electron/dubbing.webp" alt="视频配音工作区" width="100%" /></a><br />
      <strong><a href="../../docs/electron-dubbing.md">视频配音</a></strong><br />
      翻译并配音视频，让语音与原片时间对齐。
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <a href="../../docs/media/electron/voice-design.png"><img src="../../docs/media/electron/voice-design.webp" alt="在声音设计工作区中描述声音" width="100%" /></a><br />
      <strong><a href="../../docs/electron-voice-design.md">声音设计</a></strong><br />
      用文字描述你想要的声音，然后生成。
    </td>
    <td width="50%" valign="top">
      <a href="../../docs/media/electron/models.png"><img src="../../docs/media/electron/models.webp" alt="安装和管理本地语音模型" width="100%" /></a><br />
      <strong><a href="../../docs/downloading-models.md">本地模型</a></strong><br />
      自由选择引擎，只下载你需要的模型。
    </td>
  </tr>
</table>

<details>
<summary><strong>更多截图</strong></summary>

<table>
  <tr>
    <td width="50%"><a href="../../docs/media/electron/overview.png"><img src="../../docs/media/electron/overview.webp" alt="工作区概览" width="100%" /></a><br /><strong>工作区概览</strong></td>
    <td width="50%"><a href="../../docs/media/electron/model-packs.png"><img src="../../docs/media/electron/model-packs.webp" alt="模型组合" width="100%" /></a><br /><strong>模型组合</strong></td>
  </tr>
</table>

</details>

- **听写与转录** — 悬浮听写窗口与本地转录
- **朗读与批量处理** — 故事、有声书与排队任务
- **连接与自动化** — 本地 API、MCP、可复用配方与监视文件夹

从默认的 **VoiceStudio**（由 k2-fsa/OmniVoice 驱动）开始，或在模型管理器中从数十种其他引擎中挑选。参见[功能与引擎目录](../../docs/feature-catalog.md)。

## 支持平台

| 平台与状态 | 下载与加速 |
| :--- | :--- |
| <img src="../../docs/assets/platforms/apple.svg" alt="" width="20" height="20" />&nbsp;**macOS**（Apple Silicon）<br /><sub>稳定</sub> | `.dmg` · [指南](../../docs/install/macos.md)<br /><sub>Metal（MPS）</sub> |
| <img src="../../docs/assets/platforms/windows.svg" alt="" width="20" height="20" />&nbsp;**Windows 10/11**（x64）<br /><sub>稳定</sub> | `.exe` · [指南](../../docs/install/windows.md)<br /><sub>NVIDIA CUDA · [AMD ROCm（可选）](../../docs/install/windows-rocm.md) · CPU</sub> |
| <img src="../../docs/assets/platforms/linux.svg" alt="" width="20" height="20" />&nbsp;**Linux**（x64）<br /><sub>稳定</sub> | `.AppImage` · `.deb` · <a href="https://snapcraft.io/voicestudio"><img src="https://img.shields.io/badge/Snap%20Store-27272a?style=flat-square&amp;logo=snapcraft&amp;logoColor=ffffff" alt="Snap Store" height="20" /></a> · [Nix](../../docs/install/nix.md) · [指南](../../docs/install/linux.md)<br /><sub>NVIDIA CUDA · CPU</sub> |
| <img src="../../docs/assets/platforms/docker.svg" alt="" width="20" height="20" />&nbsp;**Docker**<br /><sub>稳定</sub> | [GHCR](../../docs/install/docker.md "ghcr.io/debpalash/voicestudio")<br /><sub>NVIDIA CUDA · AMD ROCm</sub> |
| <img src="../../docs/assets/platforms/android.svg" alt="" width="20" height="20" />&nbsp;**Android** 10+<br /><sub>预览版</sub> | `.apk` · [指南](../../docs/mobile.md#install)<br /><sub>设备端运行，可离线</sub> |
| <img src="../../docs/assets/platforms/apple.svg" alt="" width="20" height="20" />&nbsp;**iPhone / iPad**（iOS 17+）<br /><sub>预览版</sub> | `.ipa`（侧载） · [指南](../../docs/mobile.md#install)<br /><sub>设备端运行，可离线</sub> |
| <img src="../../docs/assets/platforms/windows.svg" alt="" width="20" height="20" />&nbsp;**Windows on ARM**<br /><sub>实验性</sub> | `.exe` · [指南](../../docs/install/windows.md)<br /><sub>CPU（x64 后端在模拟层中运行）</sub> |
| <img src="../../docs/assets/platforms/apple.svg" alt="" width="20" height="20" />&nbsp;**Intel Mac**<br /><sub>受限</sub> | `.dmg` · [原因](../../docs/install/macos.md)<br /><sub>仅应用界面，需连接远程后端</sub> |

桌面端和手机安装包都在[最新发行版](https://github.com/debpalash/VoiceStudio/releases/latest)中，Docker 镜像则发布在 GHCR。没有 GPU？应用在 CPU 上也能完整使用，只是速度较慢。原生 GGUF 引擎还能通过 Vulkan 使用 AMD、Intel 和 NVIDIA GPU；在 Windows 和 Linux 上，OmniVoice GGUF 会自动选择 GPU 和模型量化（[OmniVoice](../../docs/engines/omnivoice-gguf.md#vulkan-acceleration)、[CosyVoice](../../docs/engines/cosyvoice-gguf.md)）。各引擎在不同硬件上的速度：[性能说明](../../docs/performance.md)。

## 开始使用

**macOS / Linux**

```sh
curl -fsSL https://voicestudio.sh/install | sh
```

**Windows**（PowerShell）

```powershell
irm https://voicestudio.sh/install.ps1 | iex
```

也可以从上表下载安装包。然后打开**声音克隆**，选择声音或添加一段清晰的参考录音，输入文本并生成。下载任何模型之前，应用都会先征求你的同意。

> [!NOTE]
> 在 macOS 上看到“VoiceStudio 已损坏，无法打开”，是 Gatekeeper 拦截了应用，并不是下载文件损坏。参见 [Gatekeeper 修复方法](../../docs/install/macos.md#gatekeeper-quarantine)。

<details>
<summary><strong>更多安装方式</strong>：指定版本、当前 main、卸载、从源码安装、通过编码智能体安装</summary>

```sh
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z   # 安装指定版本
curl -fsSL https://voicestudio.sh/install | sh -s -- --main            # 构建当前 main
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall       # 卸载应用，保留数据
```

如果无法访问 `voicestudio.sh`，可直接从仓库运行脚本：
`curl -fsSL https://raw.githubusercontent.com/debpalash/VoiceStudio/main/scripts/install.sh | sh`。
前置要求与脚本行为：[安装脚本说明](../../docs/install/script.md)。

**从源码安装**

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun run dev
```

参见 [Electron 设置](../../electron/README.md)。

**通过编码智能体安装**（Claude Code、Codex、Cursor 等），粘贴：

```text
Install the VoiceStudio app on this device and verify it works, following
https://github.com/debpalash/VoiceStudio/blob/main/docs/install/agent.md
```

从 0.5.3 或更早版本（Tauri 应用）升级？请[单独安装新应用](../../docs/electron-migration.md)。

</details>

## 文档

| 需求 | 从这里开始 |
|---|---|
| 安装帮助 | [故障排查](../../docs/install/troubleshooting.md) · [模型下载](../../docs/downloading-models.md) |
| 指南 | [多说话人配音](../../docs/dubbing/multi-speaker-guide.md) · [富有表现力的语音](../../docs/expressive-speech.md) · [导出配音](../../docs/dubbing/export.md) |
| 模型与音质 | [引擎指南](../../docs/engines/README.md) · [whisper.cpp ASR](../../docs/engines/whisper-cpp.md) · [性能基准](../../docs/benchmarks.md) |
| 集成 | [本地 API](../../docs/speech-platform.md) · [MCP](../../docs/mcp.md) · [示例](../../examples/README.md) |
| 手机应用 | [iOS 与 Android](../../docs/mobile.md) |
| 开发 | [参与贡献](../../.github/CONTRIBUTING.md)（首次提交 PR 前请[签署 CLA](../../.github/CONTRIBUTING.md#contribution-licensing)）· [更新日志](../../CHANGELOG.md) |

智能体技能：`npx skills add debpalash/VoiceStudio`（音频工作用 **voicestudio**，仓库维护用 **voicestudio-maintainer**）。

## 赞助者

VoiceStudio 免费使用，背后没有付费云服务。赞助者让它持续发布新版本：赞助资金用于维护者时间、测试硬件、代码签名和发布基础设施。

<p>
    <a href="https://github.com/debpalash/OpenGTM?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="../../docs/sponsors/opengtm-dark.svg" /><img src="../../docs/sponsors/opengtm.svg" alt="OpenGTM" height="48" align="middle" /></picture></a>
    <a href="https://github.com/debpalash/Opal?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="../../docs/sponsors/opal-dark.svg" /><img src="../../docs/sponsors/opal.svg" alt="Opal" height="48" align="middle" /></picture></a>
    <a href="https://github.com/velixio/supermetry?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=readme-logo"><picture><source media="(prefers-color-scheme: dark)" srcset="../../docs/sponsors/supermetry-dark.svg" /><img src="../../docs/sponsors/supermetry.svg" alt="Supermetry" height="48" align="middle" /></picture></a>
</p>

如有赞助意向，请联系 [sponsor@voicestudio.sh](mailto:sponsor@voicestudio.sh)。

<a href="https://github.com/sponsors/debpalash"><img src="https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa?logo=githubsponsors&logoColor=white" alt="通过 GitHub Sponsors 赞助" /></a>
[Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [赞助等级与权益](../../SPONSORS.md) · [申请展示标志](https://github.com/debpalash/VoiceStudio/issues/new?template=sponsor.yml)

## 其他项目

探索 VoiceStudio 开发者打造的更多开源工具。

- **[OpenGTM](https://opengtm.palash.dev/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=related-projects)** — 在自托管的市场拓展工作区中发现企业和联系人，并完善相关数据。 [源码](https://github.com/debpalash/OpenGTM).
- **[Opal](https://opal.palash.dev/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=related-projects)** — 在一个原生开源播放器中观看、串流和浏览媒体。 [源码](https://github.com/debpalash/Opal).
- **[Supermetry](https://supermetry.com/?utm_source=voicestudio&utm_medium=sponsor&utm_campaign=strategic-sponsor&utm_content=related-projects)** — 通过自托管、无 Cookie 的分析工具了解网站和产品的使用情况。 [源码](https://github.com/velixio/supermetry).

更多项目: [debpalash](https://github.com/debpalash?tab=repositories) · [velixio](https://github.com/velixio?tab=repositories)

## 团队

### 核心团队

<table>
<tr>
<td align="center" width="200"><a href="https://github.com/debpalash"><img src="https://github.com/debpalash.png?size=160" width="80" height="80" alt="Palash Debnath"></a><br><b>Palash Debnath</b><br><sub>创始人与核心团队</sub><br><sub><a href="https://palash.dev">palash.dev</a> · <a href="https://github.com/debpalash">GitHub</a> · <a href="https://x.com/idebpalash">X</a></sub></td>
<td align="center" width="200"><a href="https://github.com/velixio"><img src="https://github.com/velixio.png?size=160" width="80" height="80" alt="Shimul Debnath"></a><br><b>Shimul Debnath</b><br><sub>核心团队</sub><br><sub><a href="https://shimul.me">shimul.me</a> · <a href="https://github.com/velixio">GitHub</a> · <a href="https://x.com/shimuldn">X</a></sub></td>
</tr>
</table>

### 贡献者

<a href="https://github.com/kevin9327"><img src="https://github.com/kevin9327.png?size=96" width="44" height="44" alt="@kevin9327" title="@kevin9327"></a> <a href="https://github.com/jaketame"><img src="https://github.com/jaketame.png?size=96" width="44" height="44" alt="@jaketame" title="@jaketame"></a> <a href="https://github.com/psiberfunk"><img src="https://github.com/psiberfunk.png?size=96" width="44" height="44" alt="@psiberfunk" title="@psiberfunk"></a> <a href="https://github.com/rudycelekli"><img src="https://github.com/rudycelekli.png?size=96" width="44" height="44" alt="@rudycelekli" title="@rudycelekli"></a> <a href="https://github.com/paoloantinori"><img src="https://github.com/paoloantinori.png?size=96" width="44" height="44" alt="@paoloantinori" title="@paoloantinori"></a> <a href="https://github.com/Chang-Jin-Lee"><img src="https://github.com/Chang-Jin-Lee.png?size=96" width="44" height="44" alt="@Chang-Jin-Lee" title="@Chang-Jin-Lee"></a> <a href="https://github.com/swadhinbiswas"><img src="https://github.com/swadhinbiswas.png?size=96" width="44" height="44" alt="@swadhinbiswas" title="@swadhinbiswas"></a> <a href="https://github.com/BichengWang"><img src="https://github.com/BichengWang.png?size=96" width="44" height="44" alt="@BichengWang" title="@BichengWang"></a> <a href="https://github.com/nirmalchatur"><img src="https://github.com/nirmalchatur.png?size=96" width="44" height="44" alt="@nirmalchatur" title="@nirmalchatur"></a> <a href="https://github.com/Shivendra-Coherent"><img src="https://github.com/Shivendra-Coherent.png?size=96" width="44" height="44" alt="@Shivendra-Coherent" title="@Shivendra-Coherent"></a> <a href="https://github.com/marreiradigital"><img src="https://github.com/marreiradigital.png?size=96" width="44" height="44" alt="@marreiradigital" title="@marreiradigital"></a> <a href="https://github.com/nvtoan0201-swe"><img src="https://github.com/nvtoan0201-swe.png?size=96" width="44" height="44" alt="@nvtoan0201-swe" title="@nvtoan0201-swe"></a>

感谢所有贡献代码、报告、翻译和想法的人。[查看全部贡献者](https://github.com/debpalash/VoiceStudio/graphs/contributors) · [参与贡献](../../.github/CONTRIBUTING.md)

## 许可证与负责任使用

[AGPL-3.0](../../LICENSE) 覆盖本应用，包括在其条款下的商业用途。模型权重、声音和生成内容各有其许可证，模型管理器会为每个模型列出（[清单](../../docs/licensing/model-inventory.md)）。只有在声音所有者许可的情况下才能克隆其声音。详情：[LICENSE-NOTICE.md](../../LICENSE-NOTICE.md)。
