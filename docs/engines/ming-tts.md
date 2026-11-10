# Ming-3.0-tts (opt-in engine)

> **Status: integrated, not yet run on real hardware by the maintainers.** The
> engine registration, dedicated-venv bootstrap, sidecar wire protocol and
> gating are unit-tested; the upstream API below was read from its source, not
> executed. It is opt-in and inert until you install it, so it cannot affect
> the default install on any platform.

[Ming-3.0-tts-0.5b](https://github.com/inclusionAI/Ming-3.0-tts) (inclusionAI /
Ant Group) is a 0.5B zero-shot voice-cloning TTS on a Qwen2.5 backbone.

- **7 languages**: Chinese, English, Japanese, Korean, French, German, Spanish.
  Any other language is rejected with the standard "can't speak" error.
- **Voice cloning from a reference clip.** Add the clip's transcript for the
  closest match; a clip alone still sets the speaker.
- **License:** MIT, for the code (GitHub `LICENSE`) and the weights (Hugging
  Face model card). The checkpoint bundles a Qwen2.5-based backbone and the
  `campplus.onnx` speaker encoder; their terms are still to be reviewed (see
  the licence registry).
- **Hardware: NVIDIA CUDA only.** Upstream documents and tests an NVIDIA H800 /
  H20 with torch 2.6 and CUDA 12.4, and loads the model in bf16 directly onto
  the GPU. There is no verified CPU, Apple Silicon (MPS) or AMD path, so the
  engine reports itself unavailable, with a reason, on those machines. Output:
  44.1 kHz mono.
- **Out of scope here:** the `tiny` / podcast model, dialect and emotion
  controls, streaming.

It runs in its **own subprocess venv** because upstream pins
`transformers==4.46.3` and `torch==2.6.0`, which conflict with VoiceStudio's
`transformers` 5.x.

## One-click install (Linux, NVIDIA)

**Model Catalogue → Ming-3.0-tts → Install** fetches the pinned upstream source
into its own folder under VoiceStudio's data directory and builds a Python 3.10
environment from upstream's `requirements.txt`. Nothing it installs touches
VoiceStudio or any other engine; **Uninstall** removes only that folder. The
~3 GB of weights download only from the Model
Catalogue's weights row; generation never downloads them. On Windows and macOS the button is not offered; see
the manual install below (Windows with an NVIDIA GPU) or pick another engine.

## Manual install

```bash
git clone https://github.com/inclusionAI/Ming-3.0-tts.git
cd Ming-3.0-tts
uv venv --python 3.10
# Windows only: install the CUDA torch 2.6.0 first (see the note below).
uv pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
uv pip install -r requirements.txt
```

> Upstream ships **no `pyproject.toml`/`setup.py`**, so there is nothing to
> `pip install -e`. The VoiceStudio sidecar puts the clone on `sys.path`.
> `requirements.txt` pins `torch==2.6.0`; on Windows PyPI serves only a CPU
> build of it, so install the CUDA build from PyTorch's `cu124` (or `cu126`)
> index **before** the requirements step. The pin is already satisfied by that
> wheel, so `uv pip install -r requirements.txt` keeps it instead of replacing
> it. Skip the torch line on Linux.

Then point VoiceStudio at the clone and restart:

- **macOS/Linux:** `export OMNIVOICE_MING_TTS_DIR=/path/to/Ming-3.0-tts`
- **Windows (PowerShell):** `[Environment]::SetEnvironmentVariable("OMNIVOICE_MING_TTS_DIR","C:\path\to\Ming-3.0-tts","User")`

Select **Ming-3.0-tts** in Model Catalogue (TTS tab → **Use**).

Optional: `OMNIVOICE_MING_TTS_MODEL` points at a local checkpoint directory
instead of `inclusionAI/Ming-3.0-tts-0.5b`. The default repo always loads its
reviewed commit; any other Hub repo id also needs
`OMNIVOICE_MING_TTS_REVISION=<40-character commit SHA>`.
`OMNIVOICE_MING_TTS_RECV_TIMEOUT_S` raises the per-request wait (default 900 s)
for a slow first model load.

## Text normalization

Upstream normalizes numbers, dates and units with `pynini`, which has no
universal wheel. The sidecar uses it when it is installed in the venv and runs
without it otherwise (upstream documents this mode).

## If it fails

A missing GPU, an incomplete venv or a failed model load reports a clear error
for that request and leaves your previous engine selectable; pick it again in
Model Catalogue or the engine picker. Reference clips are required for
generation; their transcript is optional.
