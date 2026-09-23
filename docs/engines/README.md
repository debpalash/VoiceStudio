# Engine guides

One page per engine: what it's for, what it needs, how to enable it, and its
quirks. Select engines in **Model Catalogue** (or quick-switch with
<kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>E</kbd>), or pin one with
`OMNIVOICE_TTS_BACKEND` / `OMNIVOICE_ASR_BACKEND`.

When an engine reports itself unavailable, expand its row's **Why?** panel and
use **Learn more** to jump straight to that engine's page here. The row's own
message stays deliberately generic — an availability probe can carry local
paths or credentials, so it is never shown verbatim — and the page below is
where the actual requirements and setup steps live.

The compute device (CUDA/ROCm/MPS/CPU) is auto-detected; pin it under
**Settings → Performance & Device** (or `OMNIVOICE_DEVICE`) if auto-detect
picks wrong — see [performance](../performance.md).

Measured speed/VRAM numbers live in [benchmarks](../benchmarks.md); what each
engine can do expressively in [expressive-speech](../expressive-speech.md);
sidecar disk footprints in [disk-usage](disk-usage.md); the bar a new engine
must clear in [engine-acceptance](../engine-acceptance.md).

New to VoiceStudio? Install the app first — [macOS](../install/macos.md)
(first launch needs the one-time right-click → **Open** Gatekeeper
approval), [Windows](../install/windows.md), [Linux](../install/linux.md),
[Docker](../install/docker.md).

## Text-to-speech

| Engine | Guide | Runs on | Cloning | Enabled by |
|---|---|---|---|---|
| VoiceStudio (OmniVoice) — **default** | [omnivoice](omnivoice.md) | CUDA · MPS · CPU | ✅ | installed by default |
| VoxCPM2 | [voxcpm2](voxcpm2.md) | CUDA · MPS · CPU | ✅ + voice design | `pip install "voxcpm>=2.0.3"` |
| MOSS-TTS-Nano | [moss-tts-nano](moss-tts-nano.md) | CUDA · CPU | ✅ (ref only) | clone + `uv pip install -e .` |
| KittenTTS | [kittentts](kittentts.md) | CPU | — (8 preset voices) | `pip install kittentts` |
| MLX-Audio (Kokoro, CSM, Dia, …) | [mlx-audio](mlx-audio.md) | Apple Silicon | model-dependent | `pip install mlx-audio` |
| CosyVoice 3 | [cosyvoice](cosyvoice.md) | CUDA · CPU | ✅ | clone + requirements |
| GPT-SoVITS | [gpt-sovits](gpt-sovits.md) | external server | ✅ | its own API server |
| Sherpa-ONNX | [sherpa-onnx](sherpa-onnx.md) | CUDA · CPU | — | `pip install sherpa-onnx` + model dir |
| IndexTTS 2.5 | [indextts](indextts.md) | CUDA · CPU | ✅ + emotion | one-click sidecar install |
| OmniVoice GGUF | [omnivoice-gguf](omnivoice-gguf.md) | CUDA · MPS · CPU | ✅ | bundled binary |
| Supertonic-3 | [supertonic3](supertonic3.md) | CPU | — (7 preset voices) | `uv sync --extra supertonic` + license |
| MOSS-TTS-v1.5 (8B) | [moss-tts-v15](moss-tts-v15.md) | CUDA · CPU | ✅ | clone + env var |
| dots.tts (2B) | [dots-tts](dots-tts.md) | CUDA · CPU (not Windows) | ✅ | clone + env var |
| OmniVoice (subprocess) | [omnivoice-subprocess](omnivoice-subprocess.md) | CUDA · MPS · CPU | ✅ | opt-in pick off MPS; automatic via default OmniVoice on MPS |
| PocketTTS (Kyutai) | [pockettts](pockettts.md) | CPU (not Intel Mac) | ✅ | `uv sync --extra pockettts` + license |
| Confucius4-TTS | [confucius4-tts](confucius4-tts.md) | CUDA · CPU | ✅ | clone + env var |
| audio.cpp (Breeze-TTS-2) | [audio-cpp](audio-cpp.md) | CPU + Vulkan/Metal/CUDA/HIP/ROCm where compiled | ✅ + voice design | prebuilt binary + env var (weights research/non-commercial) |

### Reference clip length

Voice Clone accepts clips up to 75 seconds and recommends 5–15 seconds of clean
speech. How much of a longer clip reaches the model depends on the engine; the
Voice Clone screen says which applies, and `GET /engines` reports it per engine
as `max_ref_seconds` and `ref_strategy`.

| Engine | Uses | From a longer clip |
|---|---|---|
| VoiceStudio (OmniVoice), OmniVoice (subprocess) | up to 20 s | picks the 15 s passage with the most speech and transcribes it (`best_window`) |
| VoxCPM2 | up to 30 s | keeps the first 30 s after edge-silence trim (`head`) |
| Other engines | not verified | the clip is passed through (`null`) |

A whole-clip transcript cannot match a passage the engine cuts out, so automatic
and saved transcripts are ignored when the clip is over the limit. A transcript
sent with a `/generate` request for an OmniVoice clip longer than 20 s, whether
an upload or a saved voice, is rejected with `[clone_ref_too_long]`: trim both
to the same passage, or leave the transcript out. The limits are the same when
an engine runs in its own environment.

## Speech-to-text

| Engine | Guide | Runs on | Best at | Enabled by |
|---|---|---|---|---|
| WhisperX | [whisperx](whisperx.md) | CUDA · CPU | dubbing (word timestamps + diarization) | installed by default |
| Faster-Whisper | [faster-whisper](faster-whisper.md) | CUDA · CPU | general transcription | installed by default |
| Faster-Whisper (isolated) | [faster-whisper-isolated](faster-whisper-isolated.md) | CUDA · CPU | unattended batches | opt-in pick |
| MLX Whisper | [mlx-whisper](mlx-whisper.md) | Apple Silicon | Mac default | `pip install mlx-whisper` |
| PyTorch Whisper | [pytorch-whisper](pytorch-whisper.md) | CUDA · MPS · CPU | ROCm hosts | installed by default |
| Parakeet TDT (NeMo) | [nemo-parakeet](nemo-parakeet.md) | CUDA · CPU | 25 languages, fast CPU | separate venv (never the app's) |
| Parakeet TDT (MLX) | [parakeet-mlx](parakeet-mlx.md) | Apple Silicon | dictation, 25 EU languages | default on mac-ARM source installs |
| Moonshine | [moonshine](moonshine.md) | CPU | edge/low-power, no timestamps | `pip install` (see guide) |
| FunASR (SenseVoice) | [funasr](funasr.md) | CUDA · CPU | 50+ languages, inline diarization | `pip install funasr` |
| Sherpa-ONNX dictation | [sherpa-onnx-asr](sherpa-onnx-asr.md) | CPU | live streaming dictation | curated model download |
| OpenAI-compatible (local or remote) | [openai-compatible-asr](openai-compatible-asr.md) | network | a configured endpoint; loopback stays local | Model Catalogue |

Speaker diarization is not an engine registry of its own — the dub pipeline
uses pyannote (HF-gated; see [diarization](../features/diarization.md)) and
FunASR can diarize inline with its `cam++` speaker model.
