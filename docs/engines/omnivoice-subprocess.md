# OmniVoice (subprocess-isolated) Engine

The `omnivoice-subprocess` engine runs the **same resident VoiceStudio model** as
the default `omnivoice` engine, but in a **crash-isolated child process** so a
wedged generation can be hard-killed and its VRAM/device reclaimed.

## Why this engine exists

An in-process `omnivoice` engine runs on the GPU worker pool. On VRAM-tight
machines a heavy generation or model load can exceed its execution budget.
When that happens the worker is "abandoned" but **cannot be killed** (Python
cannot interrupt a native torch call), so it keeps holding the GPU device until
it finishes on its own, and every later synth queues behind it and hangs
(#730 / #1190).

`omnivoice-subprocess` runs the model in a child process spawned via the same
`SubprocessBackend` primitive used by IndexTTS, Supertonic-3, and dots.tts. A
child process **can** be hard-killed: on a timeout the parent kills it
(`proc.kill()`), freeing its VRAM/device, and the next request transparently
respawns a fresh sidecar. That is the one thing the in-process engine
structurally cannot do.

## When to use it

- **Unattended / scheduled / reaction-triggered synthesis** where a stuck job
  must recover on its own instead of hanging until a manual restart.
- **VRAM-starved MPS hosts** that hit the abandoned-worker cascade.

On Apple Silicon, the default `omnivoice` id automatically uses this isolated
implementation. CUDA, ROCm, and CPU keep the in-process implementation and its
lower call overhead.

## Selecting it

- **Model Catalogue** on CUDA, ROCm, or CPU, or
- `OMNIVOICE_TTS_BACKEND=omnivoice-subprocess`

The explicit engine is an opt-in choice on CUDA, ROCm and CPU. Apple Silicon
shows only the canonical `omnivoice` choice because it automatically uses the
same process isolation there; its canonical id remains visible in APIs,
Settings and saved projects. Existing `omnivoice-subprocess` configurations
remain valid and accepted.

## Platform support

- **CUDA, AMD ROCm on Linux, MPS, and CPU** (same as the in-process
  VoiceStudio engine).
- **No extra install.** Unlike IndexTTS / dots.tts / Supertonic-3, this sidecar
  runs under VoiceStudio's own interpreter, because the goal here is crash
  isolation, not dependency isolation. If the default `omnivoice` engine works
  for you, this one is ready too.

## Tradeoffs vs the in-process `omnivoice` engine

- **Identical model and output quality.**
- Slightly higher per-call latency (one stdio round-trip per synth).
- A wedged generation is **killed and recovered** at the recv-timeout deadline
  (`OMNIVOICE_SIDECAR_RECV_TIMEOUT_S`, default 300s, aligned with the generate
  budget) instead of hanging indefinitely.
- The default Apple Silicon proxy preserves native advanced parameters,
  deterministic seeds, and longform quality settings across the process
  boundary.
- The recv-timeout deadline is per call and assumes the route's text chunking:
  `/generate` and `/v1/audio/speech` split long text into pieces of at most
  `max_chunk_chars` before calling the engine, so each call stays short. A
  single very long unchunked `generate()` can exceed the deadline and be killed;
  that is the watchdog working as intended, not a hang.

## Tuning

| Env var | Default | Purpose |
|---|---|---|
| `OMNIVOICE_SIDECAR_RECV_TIMEOUT_S` | `300` | Seconds to wait for a synth frame before hard-killing the sidecar (floored at 30s). |
| `OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S` | `300` | Idle seconds before the sidecar is reaped to free its VRAM (shared with all subprocess engines). |
