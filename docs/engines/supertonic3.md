# VoiceStudio — Supertonic-3 Engine

Supertonic-3 (Supertone Inc.) is a ~99M-parameter ONNX TTS engine covering
31 languages with 7 preset voices at native 44.1 kHz. It is CPU-only by
design — pure ONNX Runtime on the CPU execution provider, with no CUDA or
MPS path in the upstream SDK — and runs in its own sidecar process so
crashes and cold init never block the rest of VoiceStudio.

## When to pick it

- Broad language coverage on machines with no usable GPU.
- Preset-voice narration at a higher sample rate than the default engine.

## Setup

1. Install the optional dependency into VoiceStudio's environment:

   ```bash
   uv sync --extra supertonic
   ```

   Or click **Install** in **Model Catalogue → Supertonic-3**. That
   installs the same pinned wheel into the engine's own Python environment
   under VoiceStudio's data directory. Nothing it installs touches VoiceStudio
   itself or any other engine, and **Uninstall** in the same row removes only
   that folder. An install made with `uv sync` keeps working as it is.

2. **Accept the license in-app.** First use is gated behind an explicit
   acceptance dialog: the inference SDK is MIT, but the model weights are
   **OpenRAIL-M**, which carries use restrictions. The engine stays
   unavailable until you review and accept in **Model Catalogue →
   Supertonic-3**.

3. Select the engine via **Model Catalogue** (TTS tab → **Use**) or
   `OMNIVOICE_TTS_BACKEND=supertonic3`.

The first synthesis cold-downloads ~400 MB of model weights, pinned to an
exact HuggingFace revision SHA so the bytes match what the SDK was validated
against. See [downloading-models.md](../downloading-models.md).

## Voices

Seven preset voices are surfaced: `M1` (default), `M3`, `M4`, `M5`, `F3`,
`F4`, `F5`. The SDK itself accepts the full `M1`–`M5` / `F1`–`F5` set if a
caller passes one explicitly; unknown ids fall back to the default with a
log line.

In the Clone composer, choose a voice from the searchable **Preset voice** menu.
The current voice remains selected when you open the menu and press Enter;
type to filter, use the arrow keys to move, and Escape to close.

## Behaviour notes

- Output is 44.1 kHz mono.
- Runs as a long-lived sidecar: from its own environment after a one-click
  install, otherwise from VoiceStudio's (where `uv sync --extra supertonic`
  puts it). Subsequent calls reuse the warm ONNX session.
- `speed` is clamped to 0.7–2.0; quality steps clamp to 5–12.
- Language accepts a supported full name (such as Portuguese or Estonian),
  an ISO 639-1 code, or a regional tag such as `pt-BR`. Auto engages the
  SDK's multilingual fallback.

## Known limits

- **No cloning and no voice design** — preset voices only. Dub/batch jobs
  that need cloning won't select it.
- CPU-only: hardware acceleration is a property of the upstream SDK, not a
  VoiceStudio limitation.
- OpenRAIL-M weights are not covered by VoiceStudio's blanket
  commercial-use statement — review the model license terms in the
  acceptance dialog.

## Troubleshooting

- "supertonic package not installed": run the `uv sync` above or enable
  from the Model Catalogue.
- "license not accepted": open **Model Catalogue → Supertonic-3**
  and accept.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[languages.md](../languages.md),
[expressive-speech.md](../expressive-speech.md),
[disk usage](disk-usage.md).
