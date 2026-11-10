# VoiceStudio — MOSS-SoundEffect v2.0 (sound effects)

MOSS-SoundEffect v2.0 (OpenMOSS) turns a text description into a sound effect:
ambience, urban scenes, creatures, human actions, short music-like clips. It is
a 1.3B flow-matching diffusion transformer with a DAC VAE and a Qwen3 text
encoder, emits mono **48 kHz** audio up to **30 seconds** per clip, and accepts
English and Chinese descriptions. Code and weights are **Apache-2.0** (checked
on the model card and the upstream repository).

- Weights: [`OpenMOSS-Team/MOSS-SoundEffect-v2.0`](https://huggingface.co/OpenMOSS-Team/MOSS-SoundEffect-v2.0),
  pinned to revision `e35df4d82fbe87fcd5d14e5d100e349c0c3c076d` (**11.2 GB**).
- Code: [`OpenMOSS/MOSS-TTS`](https://github.com/OpenMOSS/MOSS-TTS)
  (`moss_soundeffect_v2/`), pinned to commit `934d6826b084c46a0d033402174d5f8ac4ed2519`.

This is a **desktop and web feature only**. The iOS and Android apps do not
generate sound effects and the mobile contract does not describe them.

It is **not a speech engine**: it never appears in a voice or engine picker. It
lives in **Tools → Sound effects**.

> **Opt-in, never a default.** Nothing downloads until you click **Install** on
> the Sound effects page. Everything else in VoiceStudio works without it.

## Platform support

- **NVIDIA GPU (CUDA) only.** Upstream publishes a CUDA 12.8 build of PyTorch
  and no Apple Silicon, AMD or CPU path. On any other host the page says so and
  **Generate** stays disabled. Windows and Linux both work on an NVIDIA GPU.
- Community MLX conversions for Apple Silicon exist
  (`mlx-community/MOSS-SoundEffect-v2.0-*`). They are not wired in because no
  MLX runtime for them has been reviewed.
- Allow about 22 GB of free disk (the weights plus the engine's own Python
  environment) and a GPU with enough memory for an ~11 GB bf16 model.
- The upstream pipeline compiles its network with `torch.compile` and Triton on
  the first call (minutes, and unavailable on Windows). VoiceStudio turns that
  off by default (`TORCHDYNAMO_DISABLE=1` inside the engine's own process); set
  `OMNIVOICE_SFX_COMPILE=1` to keep it on Linux.

## Using it

1. **Tools → Sound effects**. If the engine is missing, the page shows its size
   and licence and an **Install** button.
2. Describe the sound, pick a duration (1 to 30 seconds), click **Generate**.
3. Play the result and **Save** it. Takes are also in History, carry the same
   AI-generated audio mark as other VoiceStudio output when the watermark
   setting is on, and use the same GPU queue as speech generation.

The engine installs into its own folder and Python 3.12 environment (upstream
pins `numpy==1.26.4` and `transformers==4.57.1`, which cannot share an
interpreter with the app). **Uninstall** removes only that folder.

## API

- `GET /api/sfx/status` — `installed`, `available`, `supported`, `reason` (why
  Generate is disabled, else `null`), `download_bytes`, `license`, duration
  limits.
- `POST /api/sfx/generate` — `{prompt, duration, seed?}` returns
  `{id, audio_url, duration_s, gen_time_s, seed, engine}`. A duration outside
  1 to 30 seconds is refused with `422` naming the limit; a missing install or
  unsupported host returns `409`; an out-of-memory or sidecar failure returns a
  clear `500` and leaves the app and other engines running.
- Install and uninstall use the shared
  `POST`/`DELETE /engines/sidecar/moss-soundeffect/install` routes.

## Environment

| Variable | Effect |
|---|---|
| `OMNIVOICE_MOSS_SFX_DIR` | Set by the installer to the install folder. |
| `OMNIVOICE_MOSS_SFX_RECV_TIMEOUT_S` | Silence allowed from the engine (default 900 s). |
| `OMNIVOICE_SFX_COMPILE=1` | Keep upstream's `torch.compile` path. |
