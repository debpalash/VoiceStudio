# VoiceStudio — PocketTTS Engine

PocketTTS (kyutai-labs/pocket-tts, 100M parameters) is the fastest-CPU-render
pick: small, low-latency, CPU-only, with zero-shot voice cloning from a
reference clip. It covers six languages — English, French, German,
Portuguese, Italian, Spanish — with one model per language, and measures
roughly 8–9x real-time on an Apple M3 Pro.

It complements the quality engines: where they fall back to CPU, PocketTTS
is built for it. CPU-only is deliberate — upstream observes no GPU speedup
for this model.

## When to pick it

- CPU-only machines that need fast rendering *and* voice cloning.
- Latency-sensitive use (dictation-style, short utterances) in one of the
  six languages.

## Setup

1. Install the optional dependency:

   ```bash
   uv sync --extra pockettts
   ```

   Or click **Install** in **Model Catalogue → PocketTTS**. That
   installs the same pinned package into the engine's own Python environment
   under VoiceStudio's data directory, with the CPU build of PyTorch, because
   PocketTTS never uses a GPU. Nothing it installs touches VoiceStudio itself
   or any other engine, and **Uninstall** in the same row removes only that
   folder. An install made with `uv sync` keeps working as it is. The button
   is not offered on Intel Macs (see Platform notes).

2. **Accept the license in-app**
   ([#1306](https://github.com/debpalash/VoiceStudio/issues/1306)). The code
   is MIT and the weights are CC-BY-4.0, but the weights are **gated on
   HuggingFace** behind an access agreement with an acceptable-use clause.
   VoiceStudio surfaces this before first use: the engine stays unavailable
   until you review and accept in **Model Catalogue → PocketTTS**.
   You also need HuggingFace access to the gated repo (see
   [downloading-models.md](../downloading-models.md) for token setup).

3. Select the engine via **Model Catalogue** (TTS tab → **Use**) or
   `OMNIVOICE_TTS_BACKEND=pockettts`.

## Platform notes

- Works on Linux, Windows, macOS Apple Silicon — CPU only everywhere.
- **Not available on Intel Macs**: the required PyTorch version has no
  macOS x86_64 wheel. The engine reports this plainly instead of failing
  mid-install.

## Behaviour notes

- Output is 24 kHz mono.
- Six languages, one model per language, chosen by the `language` you
  request; cloning takes a short reference clip.
- Runs in a crash-isolated sidecar process: from its own environment after
  a one-click install, otherwise from VoiceStudio's (where `uv sync --extra
  pockettts` puts it). A wedged generation is hard-killed by a watchdog and its memory reclaimed —
  something an in-process engine cannot do.
- The first use downloads the gated weights; the sidecar heartbeats
  progress during the download so the watchdog doesn't fire.
- **French always renders through the 24-layer checkpoint** (`french_24l`) —
  pocket-tts ships no 6-layer French model — so French render speed is the
  24-layer figure (roughly half this page's headline speed, still faster
  than real-time), not the 6-layer one.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_POCKETTTS_RECV_TIMEOUT_S` | `600` | Sidecar response deadline in seconds (min 30; cold loads download weights) |
| `OMNIVOICE_POCKETTTS_24L` | off | When truthy, load the 24-layer checkpoint for languages that ship one (it/de/es/pt/fr) instead of the 6-layer default. Better prosody at roughly 2x render time (still faster than real-time); no effect where no 24-layer model exists (e.g. English). Opt-in: the default stays the fast model. French always uses `french_24l` — pocket-tts ships no 6-layer French model and rejects `language="french"` |

## Known limits

- No voice design, no emotion controls
  (see [expressive-speech.md](../expressive-speech.md)).
- Six languages only — for broader coverage use
  [OmniVoice](omnivoice.md) ([languages.md](../languages.md)).
- Revoking the license acceptance takes effect immediately, without a
  restart — subsequent generations refuse.

## Troubleshooting

- "pocket_tts package not installed": run the `uv sync` above.
- "license not accepted": open **Model Catalogue → PocketTTS**
  and review/accept.
- Timeouts on a slow connection: raise
  `OMNIVOICE_POCKETTTS_RECV_TIMEOUT_S` for the first (download-heavy) run.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[performance.md](../performance.md), [disk usage](disk-usage.md).

## Reference audio formats

Voice cloning accepts M4A, MP3, FLAC and WAV references through VoiceStudio's
local decoder. Add-on engines receive a compatible PCM WAV copy; install or
repair FFmpeg in **Settings → Audio tools** if the format requires it. Converted
references are cached locally and removed when the original voice is deleted
or the cache is pruned. Cache metadata uses relative source references.
