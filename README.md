<div align="center">
  <p><img src="docs/logo.png" alt="VoiceStudio logo" width="120" height="120" /></p>
  <h1>VoiceStudio</h1>
  <p>
    <a href="https://trendshift.io/repositories/28176?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-28176" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/28176" alt="VoiceStudio ranking on Trendshift" width="220" height="48" /></a>
  </p>
  <p><sub>Previously OmniVoice-Studio</sub></p>
  <h3>Clone voices, dub video, dictate, and produce long-form audio on your own hardware.</h3>
  <p>16 TTS engines · 11 ASR engines · 646-language catalogue · macOS, Windows, Linux, and Docker</p>
  <p>No account, API key, subscription, or usage meter for the local workflow.</p>

  <p>
    <a href="#install">Install</a> ·
    <a href="#features">Features</a> ·
    <a href="#comparison">Compare</a> ·
    <a href="#requirements">Requirements</a> ·
    <a href="#hardware-recommendations">Hardware</a> ·
    <a href="#engines">Engines</a> ·
    <a href="#architecture">Architecture</a> ·
    <a href="#api">API</a> ·
    <a href="#documentation">Docs</a> ·
    <a href="#faq">FAQ</a> ·
    <a href="README_CN.md"><strong>简体中文</strong></a>
  </p>

  <p>
    <a href="https://github.com/debpalash/VoiceStudio/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/debpalash/VoiceStudio/ci.yml?branch=main&style=flat-square&label=CI" alt="CI status" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/stargazers"><img src="https://img.shields.io/github/stars/debpalash/VoiceStudio?style=flat-square&color=f59e0b" alt="GitHub stars" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases"><img src="https://img.shields.io/github/downloads/debpalash/VoiceStudio/total?style=flat-square&color=8b5cf6&label=downloads" alt="Total downloads" /></a>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/github/v/release/debpalash/VoiceStudio?style=flat-square&color=10b981" alt="Latest release" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square" alt="AGPL-3.0 license" /></a>
    <a href="https://discord.gg/bzQavDfVV9"><img src="https://img.shields.io/badge/Discord-Community-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord community" /></a>
  </p>

  <p>
    <a href="https://github.com/debpalash/VoiceStudio/releases/latest"><img src="https://img.shields.io/badge/Download-macOS_·_Windows_·_Linux-10b981?style=for-the-badge" alt="Download VoiceStudio" /></a>
  </p>
</div>

<div align="center">
  <img src="docs/media/0.5.0/quick-switch.gif" alt="Switching TTS engines from the VoiceStudio status bar" width="100%" />
</div>

> [!WARNING]
> **Active beta.** Use the [latest release](https://github.com/debpalash/VoiceStudio/releases/latest) for stable work. `main` contains the newest fixes and may change between releases. Report problems through [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues).

## At a glance

| | VoiceStudio |
|---|---|
| **Workflows** | Voice cloning and design, video dubbing, dictation, stories, audiobooks, batch generation |
| **Language catalogue** | 646 TTS languages; actual coverage and quality depend on the selected engine |
| **Engines** | 16 TTS · 11 ASR · switch in Model Catalogue or with <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>E</kbd> |
| **Platforms** | macOS 13.3+ on Apple Silicon · Windows 10/11 x64 · Linux x86_64 with glibc 2.39+ |
| **Compute** | CUDA · Apple Silicon MPS/MLX · ROCm on Linux · CPU · optional remote workers |
| **Interfaces** | Desktop app · local REST/SSE/WebSocket API · OpenAI-compatible audio API · MCP Server |
| **Storage** | Voices, projects, settings, and outputs stay on the machine by default |
| **License** | AGPL-3.0 application; downloaded models keep their upstream terms |

The Voice workspace starts with three tabs: **From audio** for cloning, **By design** for creating a voice, and **Convert** for speech-to-speech conversion. Each tab displays its own workflow, with Synthesize Audio or Convert pinned below the scrolling form. The top-bar **Engines** panel combines engine selection, loaded models, and unload/flush controls; <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>E</kbd> opens it. The searchable language picker shares Dubbing’s flags and language list layout, selects one output language, and retains Auto and the full cloning catalogue. Language options flow into multiple columns when space allows. Expand **Workspaces** in the sidebar to reveal navigation labels; Escape collapses it.

Dubbing starts with file upload or URL import and nearby language choices. Its **Projects** panel lists previous dubs so they can be reopened by clicking anywhere on a card; action buttons operate independently. Advanced import options include captions and optional YouTube sign-in. Dubbing places playback controls over the video with background blur and combines the waveform and timed transcript in one compact editing surface. Drag the zoomed waveform left or right to pan; click to seek. Translation language and ISO-code controls stay synchronized; Auto clears any previous language code and dialect. Transcript items group editable text, timing and status, and voice controls into three readable rows that wrap with the panel width. Output Options stays compact with the active settings shown in its summary; expand it to change output, timing, or voice matching. Transcript, glossary, and paste controls share a toolbar above the segment editor. Project details, workflow steps, and Generate/Verify/Export actions use an unfilled header.

The Audiobook Script editor fills the available workspace beneath its markup toolbar; Voices and Book settings stay in their own tabs.

Output settings use aligned rows; review status appears before the collapsible transcript and glossary. Glossary terms have labelled entry fields and an explicit edit action. Launchpad arranges recent files and saved voices side by side when space allows, with responsive card grids and visible Open actions.

The casting board shows icon-based voice cards and searchable selectors for each speaker. Drag a card onto a speaker or choose a voice from that speaker’s menu.

<a id="install"></a>

## Install

Download a package from the [latest release](https://github.com/debpalash/VoiceStudio/releases/latest), then follow the platform guide.

| Platform | Package | Guide |
|---|---|---|
| macOS 13.3+ | Apple Silicon DMG | [Install on macOS](docs/install/macos.md) |
| Windows 10/11 | x64 MSI; choose the current-user build when listed to install without admin access | [Install on Windows](docs/install/windows.md#install-pre-built-msi) |
| Linux | AppImage, x86_64 with glibc 2.39+ | [Install on Linux](docs/install/linux.md) |
| Docker | Linux/AMD64 images; CUDA, ROCm, CPU, and worker-only GPU profiles | [Run with Docker](docs/install/docker.md) |

First launch creates a managed Python environment and downloads the default model. Later launches reuse both.

> [!NOTE]
> On macOS, first launch needs a one-time right-click, then **Open** approval. Intel Macs cannot run the local Python backend; use a [remote backend](docs/install/macos.md) instead.

### Quick Docker run

The published images are **`linux/amd64` only**. On Apple Silicon, use the
[native macOS app](docs/install/macos.md) for GPU acceleration. ARM64 hosts
should read the [architecture requirements](docs/install/docker.md#architecture)
before pulling an image.

```bash
docker run -d --platform linux/amd64 -p 127.0.0.1:3900:3900 -v omnivoice-data:/app/omnivoice_data --name voicestudio palashdeb/omnivoice-studio:stable
```

### First voice

1. Launch VoiceStudio and open **Voice Cloning**.
2. Add a clean voice sample. Three seconds works; 5 to 15 seconds usually gives a better prompt.
3. Enter text, choose a language, then select **Generate**.

> [!TIP]
> **Try without installing:** Run VoiceStudio in the cloud via the [Google Colab notebook](https://colab.research.google.com/github/debpalash/VoiceStudio/blob/main/notebooks/OmniVoice_Studio_Colab.ipynb). Explore audio quality comparisons in [benchmarks](docs/benchmarks.md) and prompt design tips in [expressive speech](docs/expressive-speech.md).

### Audio samples

Listen to sample outputs produced locally with VoiceStudio:

| Workflow | Prompt / Reference Audio | Generated Audio |
|---|---|---|
| **Voice Cloning** | [demo_voice.wav](backend/assets/samples/demo_voice.wav) | [demo_clone_output.wav](backend/assets/samples/demo_clone_output.wav) |
| **Voice Design** (US News Anchor) | *"Clear, authoritative American broadcast tone"* | [demo_voice_design_us_news_anchor.wav](backend/assets/samples/voice_design/demo_voice_design_us_news_anchor.wav) |
| **Voice Design** (UK Audiobook) | *"Warm, expressive British storytelling voice"* | [demo_voice_design_audiobook_uk_narrator.wav](backend/assets/samples/voice_design/demo_voice_design_audiobook_uk_narrator.wav) |
| **Video Dubbing** (Multilingual) | [source.src.wav](backend/assets/samples/demo/dubbing/source.src.wav) | [Spanish](backend/assets/samples/demo/dubbing/dubbed_es.src.wav) · [French](backend/assets/samples/demo/dubbing/dubbed_fr.src.wav) · [Japanese](backend/assets/samples/demo/dubbing/dubbed_ja.src.wav) · [Chinese](backend/assets/samples/demo/dubbing/dubbed_zh.src.wav) |

### Run from source

Install the [development prerequisites](.github/CONTRIBUTING.md#development-setup) (Node 20+/Bun and Python 3.11+), then:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run desktop
```

The desktop launcher configures Python dependencies on first run via `uv` automatically. Use `bun run dev` for the browser UI. See [Contributing](.github/CONTRIBUTING.md) for services, tests, and platform packages.

### If setup fails

- Run **Settings → About → Run self-check** or `uv run python backend/main.py --diagnose --deep`.
- Check [install troubleshooting](docs/install/troubleshooting.md).
- Save a scrubbed diagnostic bundle from the app when opening an issue.
- For slow generation, compare [measured benchmarks](docs/benchmarks.md) and [performance settings](docs/performance.md).

<a id="features"></a>

## Features

| Area | Included |
|---|---|
| **Voice Cloning** | Zero-shot synthesis from a short reference clip ([guide](docs/engines/README.md)) |
| **Voice Design** | Create a voice from age, accent, pitch, style, and delivery instructions ([expressive speech](docs/expressive-speech.md)) |
| **Video Dubbing** | Transcribe, translate, preserve speakers, synthesize, and export video; compact translation settings include track selection, and completed dubs flag timing issues for review ([export guide](docs/dubbing/export.md)) |
| **Stories and audiobooks** | Multi-voice scripts · EPUB/PDF import · chapter rendering · `.m4b` export |
| **[Dictation Widget](docs/features/dictation.md)** | System-wide shortcut, live transcription, optional local-LLM cleanup |
| **Vocal Isolation** | Demucs speech/background separation |
| **Speaker Diarization** | Pyannote and WhisperX speaker assignment ([guide](docs/features/diarization.md)) |
| **Batch Queue** | Queue large sets of audio and video jobs with per-job progress, or watch a local folder for new videos |
| **Model Catalogue** | Install, remove, select, and route TTS, ASR, and LLM models ([catalogue](docs/engines/README.md)) |
| **Remote Model Downloads** | Install models on enrolled remote workers with live progress ([guide](docs/downloading-models.md)) |
| **GPU Auto-Detect** | CUDA, MPS, ROCm, and CPU routing with per-engine checks ([performance](docs/performance.md)) |
| **AI Watermark** | AudioSeal embedding and detection |
| **MCP Server** | Synthesis and transcription tools for MCP clients ([guide](docs/mcp.md)) |
| **Diagnostics** | Self-checks, error journal, logs, and scrubbed support bundles ([troubleshooting](docs/install/troubleshooting.md)) |
| **Local-first** | Core creation stays local; network-backed features are explicit opt-ins |
| **Extensible** | Registry-based TTS, ASR, and plugin interfaces ([acceptance](docs/engine-acceptance.md)) |

<table>
<tr>
  <td width="50%"><img src="docs/media/0.5.0/catalogue.png" alt="VoiceStudio Model Catalogue" width="100%" /></td>
  <td width="50%"><img src="docs/media/0.5.0/gallery-save.png" alt="Saving a gallery voice as a local profile" width="100%" /></td>
</tr>
<tr>
  <td align="center"><sub>Model Catalogue: engine, device, and install state</sub></td>
  <td align="center"><sub>Gallery: save a shared voice as a local profile</sub></td>
</tr>
</table>

<a id="comparison"></a>

## Comparison

VoiceStudio trades managed cloud compute for local control. This is the practical difference:

| | **VoiceStudio** | **Typical hosted voice service** |
|---|---|---|
| **Best fit** | Private, offline, self-hosted, or high-volume work | Fast setup without local model management |
| **Data path** | Local by default; remote features are opt-in | Audio and text are processed by the provider |
| **Cost model** | Free software; you supply the hardware | Subscription, credits, or metered API use |
| **Setup** | Install the app and model weights | Create an account and use the web app or API |
| **Performance** | Depends on your engine and hardware | Provider manages compute and scaling |
| **Offline use** | Yes, after required models are installed | Usually requires a network connection |
| **Customization** | Source, engines, models, API, and routing are open | Limited to provider options |
| **Maintenance** | You manage updates, disk, and compute | Provider manages infrastructure |

<a id="requirements"></a>

## Requirements

Requirements vary by engine. These values cover the default local workflow.

| | **Minimum** | **Recommended** |
|---|---|---|
| **OS** | Windows 10 x64 · macOS 13.3 Apple Silicon · Linux x86_64 with glibc 2.39+ | Current supported OS release |
| **RAM** | 8 GB | 16 GB+ |
| **Disk** | 10 GB free | 20 GB+ SSD |
| **GPU** | Optional; CPU mode is supported | NVIDIA CUDA or Apple Silicon |
| **VRAM** | 4 GB when using a GPU | 8 GB+; large optional engines need more |
| **Python from source** | 3.11+ | 3.11 or 3.12 |

ROCm is Linux-only and opt-in. Windows AMD/Ryzen AI uses CPU. Systems with limited VRAM offload work to CPU when required. See [performance](docs/performance.md), [benchmarks](docs/benchmarks.md), and [engine disk usage](docs/engines/disk-usage.md).

<a id="hardware-recommendations"></a>

### Recommended stack by hardware

| Hardware | Recommended TTS | Recommended ASR | Why |
|---|---|---|---|
| **Apple Silicon (M1–M4)** | [MLX-Audio](docs/engines/mlx-audio.md) · [OmniVoice](docs/engines/omnivoice.md) (MPS) | [MLX Whisper](docs/engines/mlx-whisper.md) · [Parakeet MLX](docs/engines/parakeet-mlx.md) | Native unified memory, lowest latency on macOS |
| **NVIDIA GPU (8 GB+ VRAM)** | [OmniVoice](docs/engines/omnivoice.md) · [CosyVoice 3](docs/engines/cosyvoice.md) | [WhisperX](docs/engines/whisperx.md) | High-fidelity zero-shot cloning, word timestamps, diarization |
| **Low VRAM / CPU-only** | [PocketTTS](docs/engines/pockettts.md) · [Sherpa-ONNX](docs/engines/sherpa-onnx.md) · [KittenTTS](docs/engines/kittentts.md) | [Moonshine](docs/engines/moonshine.md) · [Faster-Whisper](docs/engines/faster-whisper.md) (`int8`) | Low memory footprint, optimized CPU inference |

<a id="engines"></a>

## Engines

Engine support is capability-specific. Check cloning, language, platform, memory, and license before choosing one. Full setup guides: [docs/engines](docs/engines/README.md).

<a id="tts-engines"></a>

### Text to speech

| Engine | Languages | Clone | Instruct | Linux | macOS ARM | Windows | License |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| [**VoiceStudio** (default, powered by k2-fsa/OmniVoice)](docs/engines/omnivoice.md) | 600+ | Yes | Yes | CUDA/CPU | MPS | CUDA/CPU | [AGPL-3.0](LICENSE) app · [Apache-2.0 code, CC-BY-NC weights](https://huggingface.co/k2-fsa/OmniVoice#license)³ |
| [**CosyVoice 3**](docs/engines/cosyvoice.md) | 9 + 18 dialects | Yes | Yes | CUDA/CPU | CPU | CUDA/CPU | Apache-2.0 |
| [**GPT-SoVITS**](docs/engines/gpt-sovits.md) | 5 | Yes | No | CUDA/CPU | No | CUDA/CPU | MIT |
| [**VoxCPM2**](docs/engines/voxcpm2.md) | 30 | Yes | Yes | CUDA/CPU | MPS | CUDA/CPU | Apache-2.0 |
| [**MOSS-TTS-Nano**](docs/engines/moss-tts-nano.md) | 20 | Yes | No | CUDA/CPU | CPU | CUDA/CPU | Apache-2.0 |
| [**KittenTTS**](docs/engines/kittentts.md) | English | No | No | CPU | CPU | CPU | MIT |
| [**MLX-Audio**](docs/engines/mlx-audio.md) | Model-dependent | Varies | Varies | No | MLX | No | Varies |
| [**Sherpa-ONNX**](docs/engines/sherpa-onnx.md) | 20+ | No | No | CUDA/CPU | CPU | CUDA/CPU | Apache-2.0 |
| [**IndexTTS 2.5** ⚡](docs/engines/indextts.md) | ZH · EN · JA · ES · AR | Yes | No | CUDA/CPU | CPU | CUDA/CPU | Bilibili model license¹ |
| [**OmniVoice GGUF** ⚡](docs/engines/omnivoice-gguf.md) | 600+ | Yes | Yes | CUDA/CPU | MPS/CPU | CUDA/CPU | [AGPL-3.0](LICENSE) app · [review the derivative model terms](https://huggingface.co/Serveurperso/OmniVoice-GGUF#license)³ |
| [**OmniVoice (subprocess; opt-in off MPS)** ⚡](docs/engines/omnivoice-subprocess.md) | 600+ | Yes | Yes | CUDA/CPU | MPS via default OmniVoice | CUDA/CPU | [AGPL-3.0](LICENSE) app · [Apache-2.0 code, CC-BY-NC weights](https://huggingface.co/k2-fsa/OmniVoice#license)³ |
| [**PocketTTS** ⚡](docs/engines/pockettts.md) | EN · FR · DE · PT · IT · ES | Yes | No | CPU | CPU | CPU | CC-BY-4.0, gated² |
| [**Supertonic 3** ⚡](docs/engines/supertonic3.md) | 31 | No | No | CPU | CPU | CPU | OpenRAIL-M |
| [**MOSS-TTS-v1.5** ⚡](docs/engines/moss-tts-v15.md) | 31 | Yes | No | CUDA/CPU | CPU | CUDA/CPU | Apache-2.0 |
| [**dots.tts** ⚡](docs/engines/dots-tts.md) | 24 | Yes | No | CUDA/CPU | CPU | No | Apache-2.0 |
| [**Confucius4-TTS** ⚡](docs/engines/confucius4-tts.md) | 14 | Yes | No | CUDA/CPU | CPU | CUDA/CPU | Apache-2.0 |

⚡ Installed or registered on demand.

¹ IndexTTS 2.5 requires a separate written Bilibili license above 100 million monthly active users or RMB 1 billion annual revenue. Review the [model license](https://huggingface.co/IndexTeam/IndexTTS-2.5/blob/main/LICENSE).

² PocketTTS shows its gated-access and CC-BY-4.0 terms before first use.

³ The OmniVoice snapshot also includes an audio tokenizer under separate [Boson Higgs Audio 2 and Meta Llama community terms](https://huggingface.co/k2-fsa/OmniVoice/blob/main/audio_tokenizer/LICENSE). VoiceStudio's application license does not replace model or tokenizer terms.

Clone-less engines cannot preserve a reference speaker in dubbing or pinned-voice batch jobs. VoiceStudio rejects those jobs instead of silently changing engines. Heavy engines have separate memory and platform limits; check their engine guide first.

<a id="asr-engines"></a>

### Speech to text

| Engine | ID | Languages | Best fit |
|---|---|:---:|---|
| [**WhisperX** (default)](docs/engines/whisperx.md) | `whisperx` | ~100 | Dubbing, subtitles, word-level timing |
| [**Faster-Whisper**](docs/engines/faster-whisper.md) | `faster-whisper` | ~100 | General cross-platform transcription |
| [**Faster-Whisper (isolated)**](docs/engines/faster-whisper-isolated.md) | `faster-whisper-isolated` | ~100 | Crash-isolated batch transcription |
| [**MLX Whisper**](docs/engines/mlx-whisper.md) | `mlx-whisper` | ~100 | Apple Silicon |
| [**PyTorch Whisper**](docs/engines/pytorch-whisper.md) | `pytorch-whisper` | ~100 | CUDA, MPS, and CPU fallback |
| [**Parakeet TDT**](docs/engines/nemo-parakeet.md) | `nemo-parakeet` | English + 25 EU | Fast CPU/CUDA transcription |
| [**Parakeet TDT v3 (MLX)**](docs/engines/parakeet-mlx.md) | `parakeet-mlx` | 25 EU | Apple Silicon dictation and word timestamps |
| [**Moonshine**](docs/engines/moonshine.md) | `moonshine` | English | Low-power, low-latency ONNX |
| [**FunASR**](docs/engines/funasr.md) | `funasr` | 50+ | VAD and inline diarization |
| [**sherpa-onnx** (live dictation)](docs/engines/sherpa-onnx-asr.md) | `sherpa-onnx-asr` | Model-dependent | Streaming CPU dictation |
| [**OpenAI-compatible** ⚠️ configured server](docs/engines/openai-compatible-asr.md) | `openai-compat-asr` | Server-dependent | Local gigastt/Qwen3-ASR or a remote endpoint; audio goes only to that server |

WhisperX and Faster-Whisper retry with `int8` when efficient `float16` is unavailable. Pin `ASR_COMPUTE_TYPE=int8` or `float32` only if automatic selection still fails.

<a id="architecture"></a>

## Architecture

```text
Tauri v2 desktop shell (Rust)
        │ IPC
React + Vite UI
        │ HTTP · SSE · WebSocket on localhost:3900
FastAPI backend
        ├── TTS / ASR engine registries
        ├── dubbing / audio / long-form pipelines
        ├── OpenAI-compatible API and MCP server
        └── SQLite + Alembic → omnivoice_data/
```

| Layer | Path | Responsibility |
|---|---|---|
| Desktop shell | `frontend/src-tauri/` | Window lifecycle, tray, shortcuts, updater, sidecar bootstrap |
| Frontend | `frontend/src/` | React UI, Zustand state, API and event clients, i18n |
| API | `backend/api/` | REST routes, schemas, auth boundaries, streaming |
| Core services | `backend/services/` | Generation, dubbing, audio processing, persistence |
| Engines | `backend/engines/` | Isolated and optional engine adapters |
| Worker system | `backend/worker/` | Authenticated remote compute and job transport |
| Data | `omnivoice_data/` | Projects, voices, settings, logs, and SQLite state |
| Delivery | `scripts/`, `deploy/`, `.github/workflows/` | Development, packaging, containers, releases, CI |

### Network boundary

- The desktop talks to a loopback-only backend on `localhost:3900`.
- Loopback API calls need no server key. Remote access requires a share PIN or API key.
- Remote workers and OpenAI-compatible ASR are opt-in. Loopback ASR may use HTTP and keeps audio on the machine; non-loopback endpoints require HTTPS, and redirects are not followed.
- Analytics is off until consent. If enabled, it sends allowlisted, content-free usage metadata. It never sends text, audio, file names, or projects.

<a id="api"></a>

## Local speech platform and OpenAI-compatible API

Point an OpenAI-compatible audio client at the local backend:

```diff
- base_url="https://api.openai.com/v1"
+ base_url="http://localhost:3900/v1"
```

| Endpoint | Purpose |
|---|---|
| `POST /v1/audio/speech` | TTS to `mp3`, `opus`, `aac`, `flac`, `wav`, or `pcm`; select a profile with `voice` and an engine with `model` |
| `POST /v1/audio/transcriptions` | STT to `json`, `text`, `verbose_json`, `srt`, or `vtt` |
| `WS /v1/audio/transcriptions/stream` | Live PCM/WebM transcription with partial, utterance, and session-final events |
| `GET /.well-known/voicestudio-speech` | Discover HTTP, WebSocket, MCP, and native dictation-control transports |
| `GET /v1/audio/voices` | List local voice profiles and engines |

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:3900/v1", api_key="local")

with client.audio.speech.with_streaming_response.create(
    model="tts-1",
    voice="<profile-id>",
    input="Made on my own hardware.",
    response_format="wav",
) as response:
    response.stream_to_file("speech.wav")
```

```bash
# Quick test via cURL
curl http://localhost:3900/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model": "tts-1", "input": "Made on my own hardware.", "voice": "default", "response_format": "wav"}' \
  --output speech.wav
```

The bundled Rust control sidecar lets Herdr, coding agents, VS Code, desktop apps,
and TUIs trigger the system-wide dictation flow or reuse its native text
insertion. See the [speech platform guide](docs/speech-platform.md). The full API
reference is in **Settings → OpenAPI Reference**. For LAN, Tailscale, or proxy
access, read [API authentication](docs/api-auth.md) before exposing the backend.

### Agent skills

Install the VoiceStudio skills for Claude Code, Codex, Cursor, and other [skills.sh](https://skills.sh)-compatible agents:

```bash
npx skills add debpalash/VoiceStudio
```

- `omnivoice`: synthesize speech and transcribe audio through local VoiceStudio.
- `oss-maintainer`: the repository's open-source maintenance workflow.

### Model Context Protocol (MCP)

VoiceStudio mounts an MCP server at `http://localhost:3900/mcp` for Claude Desktop, Cursor, and AI agents:

```json
{
  "mcpServers": {
    "voicestudio": {
      "url": "http://localhost:3900/mcp"
    }
  }
}
```

For clients requiring stdio transport, use the bundled local shim (`docs/mcp.json`):

```json
{
  "mcpServers": {
    "voicestudio": {
      "command": "python",
      "args": ["-m", "backend.mcp_shim"],
      "cwd": "/path/to/VoiceStudio"
    }
  }
}
```

See the [MCP guide](docs/mcp.md) for tools (`generate_speech`, `clone_voice`, `transcribe`), file streaming modes, and client bindings.

### Google Colab

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/debpalash/VoiceStudio/blob/main/notebooks/OmniVoice_Studio_Colab.ipynb)

The [notebook](notebooks/OmniVoice_Studio_Colab.ipynb) runs the app and web UI on a Colab GPU. Colab is remote compute, so uploaded audio and project data do not remain local to your machine.

<a id="documentation"></a>

## Documentation

| Need | Read |
|---|---|
| Install | [macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Linux](docs/install/linux.md) · [Docker](docs/install/docker.md) |
| Fix setup | [Troubleshooting](docs/install/troubleshooting.md) · [model downloads](docs/downloading-models.md) · [Hugging Face token](docs/setup/huggingface-token.md) |
| Choose an engine | [Engine guides](docs/engines/README.md) · [benchmarks](docs/benchmarks.md) · [expressive speech](docs/expressive-speech.md) |
| Tune hardware | [Performance](docs/performance.md) · [remote workers](docs/remote-workers.md) |
| Build integrations | [Speech platform](docs/speech-platform.md) · [Private production API](docs/production-private-api.md) · [API auth](docs/api-auth.md) · [MCP](docs/mcp.md) · [examples](examples/README.md) |
| Build VoiceStudio | [Contributing](.github/CONTRIBUTING.md) · [engine acceptance](docs/engine-acceptance.md) |
| Track changes | [Changelog](CHANGELOG.md) · [roadmap](docs/ROADMAP.md) · [latest release](https://github.com/debpalash/VoiceStudio/releases/latest) |
| Remove everything | [Uninstall guide](docs/install/uninstall.md) |

<a id="faq"></a>

## FAQ

<details>
<summary><strong>Does it work on Apple Silicon and Intel Macs?</strong></summary>

Apple Silicon is supported with MPS and MLX options. Intel Macs cannot run the local backend because current PyTorch wheels are unavailable; they can connect to a remote backend. See [macOS installation](docs/install/macos.md).
</details>

<details>
<summary><strong>How much VRAM do I need?</strong></summary>

A GPU is optional. Use 4 GB VRAM as the minimum for accelerated work and 8 GB+ for the default multi-stage workflow. Large optional engines can require 12 to 16 GB or more. Check the [benchmarks](docs/benchmarks.md) and engine guide.
</details>

<details>
<summary><strong>Why does a longer reference clip not always improve the clone?</strong></summary>

Cloning is zero-shot: the clip is a prompt, not training data. Use 5 to 15 seconds of one speaker, close to the microphone, without music, noise, or reverb. Match the tone and pace you want in the output. For training, see [data preparation](docs/data_preparation.md) and [training](docs/training.md).
</details>

<details>
<summary><strong>Can I use generated audio commercially?</strong></summary>

VoiceStudio's application license does not restrict generated audio, but it does not grant rights under a model's separate terms. The default OmniVoice repository labels its pretrained weights CC-BY-NC and includes a tokenizer under separate community terms. Review the selected model terms before commercial use.
</details>

<details>
<summary><strong>Does VoiceStudio collect data?</strong></summary>

Not unless you opt in. Analytics is off by default and skipping consent keeps it off. When enabled, the app sends allowlisted, content-free usage metadata. Text, audio, file names, voices, and projects are excluded. Change this at **Settings → Privacy**.
</details>

<details>
<summary><strong>How do I remove VoiceStudio and its data?</strong></summary>

Use `scripts/uninstall.sh` on macOS/Linux or `scripts\uninstall.ps1` on Windows. Both show a dry run before deletion. See the [uninstall guide](docs/install/uninstall.md) for every path.
</details>

## Community and contributing

- [GitHub Issues](https://github.com/debpalash/VoiceStudio/issues) for reproducible bugs and feature requests.
- [Discord](https://discord.gg/bzQavDfVV9) for setup help and project discussion.
- [Good first issues](https://github.com/debpalash/VoiceStudio/labels/good%20first%20issue) for a scoped starting point.
- [Contributing guide](.github/CONTRIBUTING.md) for setup, tests, and pull requests.

<p align="center">
  <a href="https://star-history.com/#debpalash/VoiceStudio&Date">
    <img src="https://api.star-history.com/svg?repos=debpalash/VoiceStudio&type=Date" alt="Star History Chart" width="100%" />
  </a>
</p>

## Support development

VoiceStudio is free and has no paid tier. Donations fund development and infrastructure.

[Ko-fi](https://ko-fi.com/debpalash) · [PayPal](https://paypal.me/palashCoder) · [Sponsorship details](SPONSORS.md)

## Responsible use and safety

VoiceStudio enables zero-shot voice cloning and speech generation on personal hardware. Please use it responsibly:
- **Consent:** Only clone or synthesize voices with explicit permission from the speaker.
- **Audio provenance:** VoiceStudio integrates [AudioSeal](https://github.com/facebookresearch/audioseal) imperceptible watermarking by default to detect and identify synthetic speech without altering sound quality.
- **Local privacy:** For the default local workflow, audio recordings, transcripts, voices, and projects remain strictly on your local disk; data leaves your device only when you explicitly configure remote workers or external ASR endpoints.

## License

VoiceStudio is licensed under [AGPL-3.0](LICENSE). You may run it, modify it, and use it internally. The application license itself does not restrict selling generated audio, but downloaded model and tokenizer terms may. If you modify VoiceStudio and provide that modified version as a network service, AGPL requires you to offer the corresponding source under the same license. A commercial license for VoiceStudio-owned code is available for proprietary embedding; it does not relicense third-party models. Contact **VoiceStudio@palash.dev**. See [LICENSE-NOTICE.md](LICENSE-NOTICE.md) for the plain-language scope.

Optional engines and downloaded models retain their own licenses. The bundled `omnivoice/` Python code is Apache-2.0 upstream; the default downloaded weights and audio tokenizer use separate terms.

## Acknowledgments

VoiceStudio builds on [OmniVoice](https://github.com/k2-fsa/OmniVoice), [WhisperX](https://github.com/m-bain/whisperX), [Demucs](https://github.com/facebookresearch/demucs), [Pyannote](https://github.com/pyannote/pyannote-audio), [CTranslate2](https://github.com/OpenNMT/CTranslate2), [AudioSeal](https://github.com/facebookresearch/audioseal), [Tauri](https://tauri.app), [Supertonic](https://huggingface.co/Supertone/supertonic-3), [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx), [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS), and [PocketTTS](https://kyutai.org).

<div align="center">
  <strong><a href="https://github.com/debpalash/VoiceStudio/releases/latest">Download VoiceStudio</a></strong> ·
  <a href="https://github.com/debpalash/VoiceStudio">Star the project</a> ·
  <a href="https://discord.gg/bzQavDfVV9">Join Discord</a>
</div>
