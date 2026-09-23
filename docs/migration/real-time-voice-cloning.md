# Migrating from Real-Time-Voice-Cloning (SV2TTS)

[CorentinJ/Real-Time-Voice-Cloning](https://github.com/CorentinJ/Real-Time-Voice-Cloning)
— the three-stage SV2TTS implementation (speaker encoder → Tacotron
synthesizer → WaveRNN vocoder) that introduced tens of thousands of
people to voice cloning — is archived and no longer maintained. This
guide is for its users: what maps to what in VoiceStudio, what
you gain, what you genuinely lose, and how to get your first clone
out.

## The short version

VoiceStudio is a maintained, fully-local desktop app for
macOS / Windows / Linux built around the same core idea SV2TTS
demonstrated: give it a short reference clip of a voice, get that
voice speaking any text you type. The differences are generational —
modern zero-shot engines instead of a 2019 research pipeline,
646 languages instead of English-only pretrained models, and an
installer instead of a Python environment. Like RTVC, everything runs
on your own machine: no accounts, no API keys, no cloud.

## Concept map

| Real-Time-Voice-Cloning | VoiceStudio | Notes |
|---|---|---|
| Speaker encoder + reference utterance | Reference clip in the **Voice Clone** workflow ("From audio") | No separate embedding step — zero-shot engines condition on the clip directly |
| Saved speaker embeddings (`.npy`) | **Voice Profiles** — save a clone once, reuse it everywhere | Exportable as portable `.ovsvoice` bundles |
| Synthesizer + vocoder choice (Tacotron 2 · WaveRNN / Griffin-Lim) | **TTS engine choice** — Model Catalogue | 14 engines, from CPU-realtime to GPU heavyweights; per-engine GPU preflight |
| The Toolbox GUI (`demo_toolbox.py`) | The app itself | Record or drop a clip, type text, synthesize — same loop, no `python demo_toolbox.py` |
| `demo_cli.py` / scripting your own pipeline | Local REST API (OpenAI-compatible, `http://localhost:3900/v1`), `omnivoice-infer` CLI, MCP server | See the [API section of the README](../../README.md#openai-api) |
| Training your own encoder / synthesizer / vocoder | Partial — see ["What RTVC did that VoiceStudio doesn't"](#what-rtvc-did-that-omnivoice-doesnt) | Fine-tuning the bundled model is documented; RTVC-style three-stage research training is not what this project is |

## What you gain

* **Languages.** RTVC's pretrained models were English-only. The
  default VoiceStudio engine clones across 646 languages, zero-shot —
  the same reference clip can speak Bengali, Japanese, or Swahili.
* **No Python setup.** RTVC's most-reported problems were environment
  ones (PyTorch versions, `webrtcvad` builds, missing models).
  VoiceStudio ships installers (DMG / MSI / AppImage / deb) and manages
  its own Python via `uv` when run from source.
* **Quality.** SV2TTS was a 2019 proof of concept and its author said
  as much — modern zero-shot engines (the bundled VoiceStudio model,
  CosyVoice 3, IndexTTS 2, …) are a generation ahead in naturalness
  and speaker similarity.
* **A pipeline, not just a demo.** Video dubbing (transcribe →
  translate → re-voice → MP4), audiobook and multi-voice story
  editors, batch queues, speaker diarization, vocal isolation, and
  system-wide dictation — all local.
* **Voice design without reference audio.** Describe a speaker
  (gender, age, accent, pitch, style) instead of cloning one — RTVC
  had no equivalent.
* **Maintenance.** Active releases, an issue tracker that answers,
  and a Discord that helps with setup.

## What RTVC did that VoiceStudio doesn't

Honesty where it's due:

* **A research toolbox.** RTVC let you inspect speaker embeddings,
  project them with UMAP, and watch the encoder separate speakers in
  real time. VoiceStudio is a production app, not an instrument for
  studying speaker verification.
* **Training all three stages from scratch.** RTVC documented
  training your own encoder, synthesizer, and vocoder on your own
  datasets. VoiceStudio documents [training / fine-tuning the bundled
  TTS model](../training.md) (with [data
  preparation](../data_preparation.md)), but it is not a framework
  for building new architectures.
* **Its educational value.** The repo was the companion to a thesis
  that explained SV2TTS end to end. If you're here to *learn how
  voice cloning works*, the RTVC code and thesis remain worth
  reading; the archive doesn't take that away.
* **Minimal footprint.** RTVC's pretrained models were about 1 GB.
  Expect roughly 10 GB free disk for VoiceStudio models + cache, and
  8 GB RAM minimum (a GPU is optional — CPU works, just slower).
* **License.** RTVC is MIT. VoiceStudio is AGPL-3.0 — free for
  any use including commercial, but if you modify it and serve the
  modified version over a network, you must share your changes. A
  commercial license is available for closed-source embedding — see
  the [README's License section](../../README.md#license).

One more honesty note: despite the name, RTVC's "real-time" was about
vocoding speed. VoiceStudio generation speed depends on the engine and
your hardware — some engines run realtime on CPU (KittenTTS,
MOSS-TTS-Nano), the heavier cloning engines want a GPU.

## Install

Grab the installer for your OS from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest),
then follow the guide for your platform end-to-end:

* macOS — [docs/install/macos.md](../install/macos.md)
  (Apple Silicon; Intel Macs can't run the local backend — PyTorch
  dropped Intel-Mac wheels — but can point the UI at a remote backend)
* Windows — [docs/install/windows.md](../install/windows.md)
* Linux — [docs/install/linux.md](../install/linux.md)
* Docker — [docs/install/docker.md](../install/docker.md)

If anything breaks, start with
[docs/install/troubleshooting.md](../install/troubleshooting.md) —
it covers the top install errors with exact fixes.

## Bring your reference audio over

Your RTVC reference utterances work as-is — there is no import step,
no re-encoding, no embedding extraction. Any WAV, MP3, M4A, FLAC, or
OGG file can be dropped straight in.

What makes a good reference clip (same physics as RTVC, stated
plainly):

* **Length:** cloning works from as little as ~3 seconds, but
  **5–15 seconds of continuous clean speech is the sweet spot**
  (~8 s is ideal). Voice Clone accepts up to 75 seconds, but each
  engine uses only part of a long clip — the default engine picks its
  best 15-second passage, VoxCPM2 keeps the first 30 seconds; see
  [reference clip length](../engines/README.md#reference-clip-length).
* **Clean and dry beats long.** Zero-shot cloning mirrors the
  *acoustics* of the clip, not just the voice — an echoey or noisy
  clip clones echoey and noisy. A close-mic recording in a quiet room
  wins every time.
* **One speaker, no music.** If your source has background music or
  multiple speakers, the app's vocal isolation (Demucs) and
  diarization can separate them — but a clean solo clip is still the
  best input.

If a great clip still isn't close enough and you have *hours* of
recordings, the step up isn't a longer reference — zero-shot
conditioning stops using audio past a short window — it's offline
fine-tuning of the bundled model on your own dataset: see
[training / fine-tuning](../training.md) and
[data preparation](../data_preparation.md). Technical, command-line,
GPU-required — but it's the trained-on-your-voice path.

## Your first clone

1. Launch the app and pick the **Voice Clone** card on the Launchpad
   (or open the **Voice** workspace and set "Define voice" to
   **From audio**).
2. Drop in a reference clip — or click **Record** and read a couple
   of sentences.
3. Type what the voice should say, pick a language, and hit
   **Synthesize Audio**.
4. Happy with it? **Save as Voice Profile** — the voice is now
   reusable across generation, dubbing, stories, and the API, no
   re-upload needed.

For what the generation knobs do (steps, speed, denoise, chunking
for long text), see
[docs/generation-parameters.md](../generation-parameters.md). To try
a different engine for the same clip, switch in **Settings →
Engines** — the choice applies everywhere synthesis happens.

## If you scripted RTVC

`demo_cli.py` users have three local, keyless replacements:

* **OpenAI-compatible REST API** — the backend serves
  `POST /v1/audio/speech` on `http://localhost:3900/v1`; the `voice`
  field accepts your saved voice-profile IDs. Existing OpenAI-SDK
  code points at it with a one-line `base_url` change.
* **CLI** — from a source checkout, `omnivoice-infer` (and
  `omnivoice-infer-batch`) run the bundled engine directly.
* **MCP server** — expose your voices to Claude, Cursor, or any MCP
  client; see [docs/mcp.md](../mcp.md).

## Getting help

Setup questions get answered in
[Discord](https://discord.gg/bzQavDfVV9) (usually within hours), bugs
go to
[GitHub Issues](https://github.com/debpalash/VoiceStudio/issues)
— see [SUPPORT.md](../../.github/SUPPORT.md) for what to include. Welcome
over.
