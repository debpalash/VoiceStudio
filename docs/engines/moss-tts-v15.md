# VoiceStudio — MOSS-TTS-v1.5 Engine

MOSS-TTS-v1.5 (OpenMOSS) is an **8B** flagship zero-shot TTS — a Qwen3-8B
language backbone plus a 1.6B audio codec. It covers **31 languages**, does
zero-shot voice cloning, token-level duration control and inline
`[pause Ns]` markers. Released under **Apache-2.0** (code + weights).

It runs in its own subprocess **and its own Python venv** with
`transformers==5.0.0`, isolated from the VoiceStudio parent process which
pins `transformers>=5.3`. This is the same isolation primitive used by
[IndexTTS-2](indextts.md): the two `transformers` pins cannot share one
interpreter, so MOSS runs behind
`backend/services/subprocess_backend.py::SubprocessBackend`.

> **Opt-in, and never a default.** MOSS-TTS-v1.5 is selected explicitly in
> **Model Catalogue** (or `OMNIVOICE_TTS_BACKEND=moss-tts-v15`). It is
> not part of the default install and does not change VoiceStudio's
> out-of-the-box behaviour on any platform.

## Hardware

- **VRAM/RAM:** an 8B model. The upstream llama.cpp pipeline fits the 8B on
  8 GB GPUs when quantized; the bf16 Transformers path used here is ~16 GB
  of weights, so a 16 GB+ GPU is the realistic CUDA target. It also runs on
  **CPU** (fp32) — correct but slow.
- **Device:** the sidecar uses a runtime-available PyTorch CUDA/ROCm, XPU,
  or registered NPU backend, otherwise CPU. The isolated engine venv needs the
  matching torch/vendor integration. A failed accelerator probe falls back to
  CPU, including in older venvs without the unified accelerator API. MPS still uses CPU. XPU/NPU routing is
  covered by mocked loader tests; physical-device synthesis has not been
  validated by this change.

## One-click install

On a machine with an NVIDIA GPU, **Model Catalogue → MOSS-TTS-v1.5 →
Install** does every step below for you. It installs into its own folder under VoiceStudio's data directory, with its own Python environment. Nothing it installs touches VoiceStudio itself or any other engine, so you can switch to it and back without breaking what already worked. **Uninstall** in the same row removes only that folder. The ~16 GB of weights still
download on first synthesis. On a CPU-only host the button is not offered; use
the manual install.

The first synthesis downloads the weights, which takes a while on a slow
connection. The generation stays alive while the download makes progress;
if a stalled download runs out of time, raise the compute-time budget in
**Settings → Performance & Device** and try again.

The TTS model and its audio codec load from separate pinned Hugging Face
snapshots: `OpenMOSS-Team/MOSS-TTS-v1.5` at
`cdd3b911b1585e3f2dbc7775ef10f9926f58850a` and
`OpenMOSS-Team/MOSS-Audio-Tokenizer` at
`3cd226ba2947efa357ef453bcad111b6eafba782`. Existing cached files are reused;
after downloading, synthesis also works offline.

## Install

MOSS-TTS-v1.5 is **not** bundled (the model is large and the package pins a
conflicting `transformers`). VoiceStudio ships a sidecar runner that loads it
into an isolated venv on demand.

1. Clone the MOSS-TTS repo on disk:

   ```bash
   git clone https://github.com/OpenMOSS/MOSS-TTS.git
   ```

2. Install the editable package into a fresh venv. Use
   `uv pip install -e ".[torch-runtime]"` — **never** `uv sync --all-extras`,
   which would overwrite VoiceStudio's lock file with `transformers==5.0` and
   break the parent process. The `torch-runtime` extra is CUDA (`+cu128`):

   ```bash
   cd MOSS-TTS
   uv venv .venv
   uv pip install -e ".[torch-runtime]" "torchcodec===0.8.1"      --extra-index-url https://download.pytorch.org/whl/cu128      --index-strategy unsafe-best-match
   ```

   The extra pins `torch==2.9.1+cu128`, which is published only on PyTorch's
   own index, so the `--extra-index-url` is required — without it uv reports
   the requirements as unsatisfiable on every host.
   The exact `torchcodec===0.8.1` requirement selects the CPU audio decoder;
   `==0.8.1` also admits the CUDA build, which needs additional NVIDIA NPP
   libraries even when decoding reference audio on the CPU.

   On a **non-CUDA / CPU host** (e.g. Apple Silicon), install plain
   `torch`/`torchaudio`/`transformers==5.0.0`/`torchcodec===0.8.1` into the venv instead of the
   `+cu128` extra (the auto-bootstrap below only targets CUDA hosts).

3. The ~16 GB weights download from HuggingFace on first synthesize. The
   parent forwards `HF_HOME` / `HF_HUB_CACHE` to the sidecar so the cache is
   shared with the rest of VoiceStudio's downloads.

4. Set `OMNIVOICE_MOSS_TTS_V15_DIR` to the repo root (the directory that
   contains `pyproject.toml`):

   ```bash
   # macOS / Linux
   echo 'export OMNIVOICE_MOSS_TTS_V15_DIR=$HOME/code/MOSS-TTS' >> ~/.zshrc
   source ~/.zshrc
   ```

   ```powershell
   # Windows PowerShell
   [Environment]::SetEnvironmentVariable("OMNIVOICE_MOSS_TTS_V15_DIR","$env:USERPROFILE\code\MOSS-TTS","User")
   ```

5. Restart VoiceStudio. MOSS-TTS-v1.5 appears in **Model Catalogue** with
   `available: true` and `isolation_mode: subprocess`.

## Venv resolution order

VoiceStudio probes for a usable MOSS Python interpreter in this priority
order (see `backend/engines/moss_tts_v15/bootstrap.py`):

1. **`${OMNIVOICE_MOSS_TTS_V15_DIR}/.venv/`** — your existing clone's venv.
   Highest priority, so a power user who already set MOSS up gets zero
   re-install.
2. **`backend/engines/moss_tts_v15/.venv/`** — VoiceStudio's own venv,
   created on demand by step 3.
3. **Lazy bootstrap** — if neither venv exists, VoiceStudio runs `uv venv`
   then `uv pip install --python <python> -e "${DIR}[torch-runtime]"`.
   Requires `OMNIVOICE_MOSS_TTS_V15_DIR`; raises a clear error otherwise.
   On a non-CUDA host the `+cu128` extra cannot resolve — set the venv up
   manually per step 2.

## Voice cloning

Pass a reference clip as `ref_audio`. MOSS's zero-shot clone mode needs only
the audio (no transcript). Without a reference, MOSS synthesizes in its own
default voice. `duration` (seconds) maps to MOSS's `tokens` argument at
~12.5 tokens/second.

## Optional env knobs

| Variable | Default | Purpose |
|----------|---------|---------|
| `OMNIVOICE_MOSS_TTS_V15_DIR` | — | Path to the MOSS-TTS clone (required). |
| `OMNIVOICE_MOSS_TTS_V15_MODEL` | `OpenMOSS-Team/MOSS-TTS-v1.5` | Advanced HF repo override. Custom repositories execute their modelling code and are rejected unless both safeguards below are set. Configure `HF_ENDPOINT` for a mirror instead. |
| `OMNIVOICE_MOSS_TTS_V15_REVISION` | — | Immutable 40-character commit SHA required with a custom model repository. |
| `OMNIVOICE_MOSS_TTS_V15_TRUST_REMOTE_CODE` | — | Set to `1` only after auditing a custom repository's Python code. The reviewed built-in repository needs no opt-in. |
| `OMNIVOICE_MOSS_TTS_V15_ATTN` | `sdpa` | Attention impl; set `flash_attention_2` on Ampere+ CUDA with `flash-attn` installed. |

## Common errors

### `MOSS-TTS-v1.5 venv not found. Set OMNIVOICE_MOSS_TTS_V15_DIR ...`

You haven't pointed VoiceStudio at a MOSS-TTS clone yet. Follow **Install**.

### `uv pip install -e failed ... '[torch-runtime]' extra (cu128) cannot resolve`

You're on a non-CUDA host. The upstream `torch-runtime` extra is CUDA-only;
set up the venv manually with plain `torch`/`transformers==5.0.0` (step 2).

### `Could not load libtorchcodec` or `libnppicc.so.12` during voice cloning

An older installation may contain the CUDA TorchCodec build. Stop the engine,
then replace only its audio decoder, leaving the model weights and other
packages in place:

```bash
uv pip install --python <moss-venv-python> --index-url https://pypi.org/simple --reinstall-package torchcodec --no-deps "torchcodec===0.8.1"
```

Use the Python executable in the MOSS engine's own `.venv`: `bin/python` on
macOS/Linux or `Scripts/python.exe` on Windows. New one-click and lazy
bootstrap installs already select this decoder.

## License

The upstream model cards identify both the TTS model and audio codec as
Apache-2.0. VoiceStudio records them as separate components; their full terms
remain unreviewed, so accept the displayed model licence notice before use.
See the upstream [README](https://github.com/OpenMOSS/MOSS-TTS/blob/main/README.md).

---

MOSS-TTS-v1.5 runs in a dedicated sidecar venv (it pins `transformers==5.0`,
which conflicts with the parent's `transformers>=5.3`). For why that adds
disk and how uv keeps the cost down, see
[Engine venvs & disk usage](disk-usage.md).
