# VoiceStudio — Sherpa-ONNX Engine

Sherpa-ONNX (k2-fsa/sherpa-onnx) is a unified C++ ONNX runtime that wraps
20+ TTS model families (VITS, MeloTTS, Piper, Kokoro, Matcha, and more)
behind one API, with pre-built wheels for Linux, Windows, and macOS (x86 and
ARM). You bring the model: point VoiceStudio at any downloaded sherpa-onnx
TTS model directory.

## When to pick it

- You want a specific community model (e.g. a Piper or VITS voice for your
  language) that no other engine hosts.
- You need a dependable CPU engine with optional CUDA acceleration.

## Setup

1. Install the runtime:

   ```bash
   pip install sherpa-onnx
   ```

2. Download a TTS model from the
   [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases)
   and unpack it somewhere permanent.

3. Point VoiceStudio at the model directory and restart:

   ```bash
   export OMNIVOICE_SHERPA_MODEL=/path/to/model-dir
   ```

4. Select the engine via **Model Catalogue** (TTS tab → **Use**) or
   `OMNIVOICE_TTS_BACKEND=sherpa-onnx`.

The selected directory must contain `tokens.txt` and one model: either
`model.onnx`, or the voice-named `.onnx` file found in a Piper archive. When
present, `espeak-ng-data/` is used for Piper phonemization and `lexicon.txt` is
used by lexicon-driven VITS models such as MeloTTS and Chinese voices; neither
requires another setting.

Sherpa-ONNX has no bundled default model, so the engine reports unavailable — with the
reason — until `OMNIVOICE_SHERPA_MODEL` points at a valid directory. (Before
this gate, selecting the engine unconfigured produced a failure mislabeled
as out-of-memory —
[#919](https://github.com/debpalash/VoiceStudio/issues/919).)

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_SHERPA_MODEL` | unset | Path to the unpacked directory containing `tokens.txt` and either `model.onnx` or one voice-named `.onnx` file. |

### Example: a US English Piper voice

```bash
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-lessac-medium.tar.bz2
tar xjf vits-piper-en_US-lessac-medium.tar.bz2
export OMNIVOICE_SHERPA_MODEL="$PWD/vits-piper-en_US-lessac-medium"
```

Any other Piper voice is set up identically; just swap in its archive name and
the folder it unpacks to.

## Licences

Models supplied through this directory remain subject to their own licence
terms. Because VoiceStudio does not provide or catalogue this model, its
catalogue licence prompt does not cover it. Read the model card included in
the unpacked folder before using or distributing generated audio.

## Behaviour notes

- Output defaults to 22.05 kHz (the VITS default); once a model is loaded,
  its own sample rate is used.
- CPU is the universal baseline; the CUDA onnxruntime provider is available
  on Linux/Windows installs.
- **No cloning**: voices come from the model itself. Multi-speaker VITS
  models select a voice by numeric speaker id; speed is supported.
- Languages depend entirely on the model you download.

## Known limits

- One model at a time — switching models means changing
  `OMNIVOICE_SHERPA_MODEL` and restarting.
- No voice design, no reference-audio cloning, no emotion controls
  (see [expressive-speech.md](../expressive-speech.md)).

## Troubleshooting

- "OMNIVOICE_SHERPA_MODEL not set" / "No model.onnx in …": follow Setup
  above — the variable must point at the *unpacked* model directory, not
  the downloaded archive. A folder with multiple `.onnx` files and no exact
  `model.onnx` is also rejected because the intended voice is unclear.
- Number reading depends on whether the model language has a supported text
  normalizer. Comma decimals such as `2,5` reach the voice as digits; write
  them as words (for example, “two point five”) for more reliable speech.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[languages.md](../languages.md),
[disk usage](disk-usage.md).
