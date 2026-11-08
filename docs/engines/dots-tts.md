# VoiceStudio — dots.tts Engine

dots.tts (rednote-hilab) is a **2B** fully-continuous autoregressive TTS,
widely cited as one of the strongest open zero-shot voice-cloning models. It
covers **24 languages**, emits **48 kHz** audio, and is released under
**Apache-2.0** (code + checkpoints).

It runs in its own subprocess **and its own Python venv** with
`transformers==4.57.1`, isolated from the VoiceStudio parent process which
pins `transformers>=5.3` — the same isolation primitive used by
[IndexTTS-2](indextts.md) and [MOSS-TTS-v1.5](moss-tts-v15.md).

> **Opt-in, and never a default.** dots.tts is selected explicitly in
> **Model Catalogue** (or `OMNIVOICE_TTS_BACKEND=dots-tts`). It is not
> part of the default install.

## Platform support

- **Linux / macOS only.** dots.tts's upstream package declares Linux and
  macOS classifiers and has **no Windows install path**. On Windows this
  engine reports itself unavailable in **Model Catalogue**; use the
  [audio.cpp dots.tts engine](audio-cpp.md#dotstts) (`audiocpp-dots`)
  instead, which runs the same model natively on Windows, macOS and Linux.
- **No MPS.** Upstream device selection is CUDA-or-CPU with no Metal branch,
  so on Apple Silicon the official package runs on **CPU** (slow but
  correct). A faster Apple-Silicon path exists only via community MLX ports,
  which VoiceStudio does not auto-wire.
- **VRAM:** ~9 GB checkpoint; a 12–16 GB CUDA GPU is the realistic target.

## One-click install

On Linux and macOS, **Model Catalogue → dots.tts → Install** does the
steps below for you. It installs into its own folder under VoiceStudio's data directory, with its own Python environment. Nothing it installs touches VoiceStudio itself or any other engine, so you can switch to it and back without breaking what already worked. **Uninstall** in the same row removes only that folder. It is not offered on Windows, where upstream
publishes no install. The ~9 GB checkpoint still downloads on first synthesis.

The first synthesis downloads the weights, which takes a while on a slow
connection. The generation stays alive while the download makes progress;
if a stalled download runs out of time, raise the compute-time budget in
**Settings → Performance & Device** and try again.

The installer and lazy bootstrap pass the constraints file as an encoded local
file URL. This preserves spaces (including macOS `Application Support`) and
reserved characters without moving your engine or model data.

### Dependencies and retrying a failed install

VoiceStudio replaces the unavailable upstream `gradio==6.17.0` pin with
`6.17.3` and the yanked `transformers==4.57.0` pin with `4.57.1` in a generated
`.voicestudio-compatible.txt` beside the original constraints file. Original
constraints, custom/newer pins, downloaded models and working environments stay
intact. Managed installation and lazy bootstrap both use Python 3.11.

The text normalizer depends on Pynini. Linux x86-64 has binary wheels; macOS
and other hosts may need to build it against **OpenFst with FAR support** and a
C++ compiler. On macOS install Xcode command-line tools (`xcode-select --install`)
and `brew install openfst`. Set the following in the terminal launching
VoiceStudio (or Settings → Environment) if Homebrew headers/libraries are not
already on the compiler search path:

```bash
export CPLUS_INCLUDE_PATH="$(brew --prefix openfst)/include${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"
export LIBRARY_PATH="$(brew --prefix openfst)/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
```

On Debian/Ubuntu source-build hosts, install
`sudo apt install libfst-dev libfst-tools build-essential`. Other distributions
need the equivalent OpenFst development package, including FAR libraries.
Retry **Install** after preparing these dependencies; it reuses the existing
source, environment and download cache. A missing `fst/util.h` is a compiler
prerequisite failure, not a network error.

## Install

dots.tts is **not** bundled (large checkpoint + conflicting `transformers`).

1. Clone the dots.tts repo on disk:

   ```bash
   git clone https://github.com/rednote-hilab/dots.tts.git
   ```

2. Install the editable package into a fresh venv with the upstream
   compatible constraints below. Use `uv pip install -e . -c <constraints>` —
   **never** `uv sync --all-extras`, which would overwrite VoiceStudio's lock
   file with `transformers==4.57` and break the parent process:

   ```bash
   cd dots.tts
   uv venv .venv --python 3.11
   # Repair the two known broken pins without editing upstream's file.
   sed -e 's/^gradio==6.17.0$/gradio==6.17.3/' \
       -e 's/^transformers==4.57.0$/transformers==4.57.1/' \
       constraints/recommended.txt > constraints/.voicestudio-compatible.txt
   uv pip install -e . -c constraints/.voicestudio-compatible.txt
   ```

3. The ~9 GB checkpoint downloads from HuggingFace on first synthesize. The
   parent forwards `HF_HOME` / `HF_HUB_CACHE` to the sidecar so the cache is
   shared with the rest of VoiceStudio's downloads.

4. Set `OMNIVOICE_DOTS_TTS_DIR` to the repo root (the directory that
   contains `pyproject.toml` and `constraints/`):

   ```bash
   # macOS / Linux
   echo 'export OMNIVOICE_DOTS_TTS_DIR=$HOME/code/dots.tts' >> ~/.zshrc
   source ~/.zshrc
   ```

5. Restart VoiceStudio. dots.tts appears in **Model Catalogue** with
   `available: true` and `isolation_mode: subprocess`.

## Venv resolution order

VoiceStudio probes for a usable dots.tts Python interpreter in this priority
order (see `backend/engines/dots_tts/bootstrap.py`):

1. **`${OMNIVOICE_DOTS_TTS_DIR}/.venv/`** — your existing clone's venv.
2. **`backend/engines/dots_tts/.venv/`** — VoiceStudio's own venv, created on
   demand by step 3.
3. **Lazy bootstrap** — `uv venv` then `uv pip install -e <clone> -c
   <constraints>`. When repairs are needed, `<constraints>` is the generated
   `<clone>/constraints/.voicestudio-compatible.txt`; otherwise it remains
   `<clone>/constraints/recommended.txt`, preserving custom or newer pins.
   Requires `OMNIVOICE_DOTS_TTS_DIR`.

## Voice cloning

For best fidelity ("continuation cloning"), pass **both** a reference clip
(`ref_audio`) and its exact transcript (`ref_text`). A reference clip alone
does x-vector-only cloning. Keep the reference ~10 s. Upstream requires the
reference audio whenever a transcript is given, so VoiceStudio drops a stray
`ref_text` that arrives without `ref_audio`.

## Optional env knobs

| Variable | Default | Purpose |
|----------|---------|---------|
| `OMNIVOICE_DOTS_TTS_DIR` | — | Path to the dots.tts clone (required). |
| `OMNIVOICE_DOTS_TTS_MODEL` | `rednote-hilab/dots.tts-soar` | Checkpoint override (`-base`, `-soar`, `-mf`). |
| `OMNIVOICE_DOTS_TTS_PRECISION` | `bfloat16` (CUDA) / `float32` (CPU) | Inference precision. |
| `OMNIVOICE_DOTS_TTS_OPTIMIZE` | `0` | `1` enables `torch.compile` (slower first call, faster after). |

> Using the `dots.tts-mf` (MeanFlow-distilled) checkpoint? It's tuned for
> **4** flow-matching steps — pass `num_step=4`.

## Common errors

### `dots.tts is not supported on Windows ...`

Upstream is Linux/macOS only. Use the [audio.cpp dots.tts engine](audio-cpp.md#dotstts),
which runs natively on Windows.

### `dots.tts venv not found. Set OMNIVOICE_DOTS_TTS_DIR ...`

You haven't pointed VoiceStudio at a dots.tts clone yet. Follow **Install**.

## License

Apache-2.0 (code and checkpoints). See the upstream
[README](https://github.com/rednote-hilab/dots.tts/blob/main/README.md).

---

dots.tts runs in a dedicated sidecar venv (it pins `transformers==4.57`,
which conflicts with the parent's `transformers>=5.3`). For why that adds
disk and how uv keeps the cost down, see
[Engine venvs & disk usage](disk-usage.md).

The upstream runtime selects CUDA or CPU internally; a ROCm (HIP) torch in the
sidecar venv counts as CUDA, so on Linux ROCm the one-click install takes the
ROCm build of the torch version upstream's constraints pin. Automatic precision follows
that selection: bfloat16 on CUDA, float32 otherwise, including XPU/NPU/MPS hosts
where this runtime executes on CPU. `OMNIVOICE_DOTS_TTS_PRECISION` remains an
explicit override. If the CUDA availability probe raises, the automatic precision
default stays float32; upstream remains responsible for its device selection.
