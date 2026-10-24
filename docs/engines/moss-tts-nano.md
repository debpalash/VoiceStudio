# VoiceStudio — MOSS-TTS-Nano Engine

MOSS-TTS-Nano (OpenMOSS) is the low-resource, broad-language pick: a
100M-parameter autoregressive codec LM that runs realtime on a 4-core CPU —
no GPU required — with native 48 kHz output and 20 languages under an
Apache-2.0 license. It fills the "runs on a fanless laptop" tier while still
covering languages like Arabic, Hebrew, Persian, Korean, and Turkish.

## When to pick it

- CPU-only or low-power hardware, but you still need cloning and non-English
  coverage.
- Your language is among: Chinese, English, German, Spanish, French,
  Japanese, Italian, Hebrew, Korean, Russian, Persian, Arabic, Polish,
  Portuguese, Czech, Danish, Swedish, Hungarian, Greek, Turkish.

## Setup

The package is **not on PyPI** — install it from the upstream repo into
VoiceStudio's Python environment:

```bash
git clone https://github.com/OpenMOSS/MOSS-TTS-Nano.git
cd MOSS-TTS-Nano
uv pip install -e .
```

Then select the engine via **Model Catalogue** (TTS tab → **Use**) or
`OMNIVOICE_TTS_BACKEND=moss-tts-nano`.

## Model selection

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_MOSS_TTS_MODEL` | `OpenMOSS-Team/MOSS-TTS-Nano` | HuggingFace checkpoint to load |

The first use downloads the weights (retried once on a truncated download).
See [downloading-models.md](../downloading-models.md).

## Behaviour notes

- **Cloning is reference-only**: pass a reference clip. Style instructions,
  preset speakers, and speed control are not supported and are silently
  ignored, so mixed-engine call sites keep working.
- The model emits 48 kHz stereo; VoiceStudio downmixes to mono, matching the
  rest of the pipeline (the dub mixer treats TTS output as mono per
  segment).
- Runs on CPU or CUDA.

## Upstream is unpinned

The upstream repo is installed straight from git with no pinned release, and
the model class it exports has changed before
([#1287](https://github.com/debpalash/VoiceStudio/issues/1287)). VoiceStudio
therefore verifies that a usable model class actually exists — not just that
the package imports — before reporting the engine as ready. If the engine
shows unavailable with a "does not expose a usable model class" message,
pull the latest upstream and re-run `uv pip install -e .`, or open an issue
with the version you have.

The one-click install is not affected: it pins a reviewed commit
(`8b7bcc93`, 2026-09-06) and drives the runtime that commit ships.

## Known limits

- No voice design, no instruct, no speed control — cloning from a reference
  clip only.
- Quality sits below the large engines; see
  [benchmarks.md](../benchmarks.md).

## One-click install

Click **Install** in **Model Catalogue → MOSS-TTS-Nano**.
VoiceStudio clones a reviewed upstream commit into its own folder under the
data directory, gives it its own Python environment (the CUDA build of
PyTorch on an NVIDIA GPU), and runs it there in a separate process.

Nothing it installs touches VoiceStudio itself or any other engine, and
**Uninstall** in the same row removes only that folder. The button is not
offered on Intel Macs, where the PyTorch version it pins has no build.

The first synthesis downloads the model and its audio tokenizer. The
generation stays alive while the download makes progress; if a stalled
download runs out of time, raise the compute-time budget in
**Settings → Performance & Device** and try again.

The text tokenizer reuses the configured Hugging Face Hub cache, including
`HF_HOME` / `HF_HUB_CACHE` overrides. Loading it preserves `HF_MODULES_CACHE`
instead of creating a nested cache beside the downloaded model code, keeping
the app's short Windows cache paths effective. Tokenizers already downloaded
into an older nested cache remain usable offline without another download.

## Troubleshooting

- "moss_tts_nano package not installed": run the clone + `uv pip install -e .`
  steps above.
- Entry-point errors after an upstream update: see "Upstream is unpinned"
  above.
- General issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [languages.md](../languages.md),
[expressive-speech.md](../expressive-speech.md),
[disk usage](disk-usage.md).

### Repairing older managed installations

Older managed installs may lack the audio backend needed to read reference clips.
Model Catalogue now detects their outdated dependency marker and offers Install
again. Select it to repair dependencies in the existing environment; the checkout
and cached models are retained. Checking installation status never downloads
anything. Verification requires an available torchaudio audio backend, including
when Python optimization is enabled. User-managed environments remain under your
control: install `soundfile` in that engine’s virtual environment.
