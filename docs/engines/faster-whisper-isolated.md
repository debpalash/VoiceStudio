# VoiceStudio — Faster-Whisper (Crash-Isolated) Engine

The same CTranslate2 Whisper engine as [faster-whisper](faster-whisper.md),
run in a **separate child process** ("sidecar"). CTranslate2's GPU teardown
can segfault — the endemic faster-whisper crash — and a hung or crashed
transcribe in-process takes the whole backend down with it. Isolated, the
child can crash or be force-killed to reclaim a hung transcribe and its VRAM
while the backend stays up
([#730](https://github.com/debpalash/VoiceStudio/issues/730)).

There is nothing extra to install: the sidecar reuses the app's own venv —
only the process boundary is new.

## Selecting it

- **Model Catalogue**, ASR tab → **Use** on the crash-isolated row, or
- pin it with `OMNIVOICE_ASR_BACKEND=faster-whisper-isolated`.

It is never picked by auto-detect — it's an explicit opt-in escape hatch.

## Best at

- **Long batch runs** where one bad file must not kill the backend.
- Machines where in-process faster-whisper has crashed or hung before:
  a sidecar crash fails only that job, and the next transcribe respawns a
  fresh sidecar automatically.

## Platform support

Same as faster-whisper: CUDA float16 or CPU int8 on macOS, Windows, and
Linux. The sidecar picks cuda/cpu itself and walks the same
float16 → int8_float16 → int8 degrade chain on GPUs without efficient fp16
([#551](https://github.com/debpalash/VoiceStudio/issues/551)).

## Model selection

- `ASR_MODEL_FASTER` — the shared model selection, same as the in-process
  engine: set it once and both variants load the same weights.
- `ASR_MODEL_FW` — optional sidecar-only override; when set it wins over
  `ASR_MODEL_FASTER` for this engine. Default `large-v3`.
- `ASR_COMPUTE_TYPE` — optional: pin the sidecar to one CTranslate2 compute
  type instead of the automatic degrade chain.

Weights download on first load — see
[downloading-models](../downloading-models.md).

## Trade-offs and quirks

- **Slightly slower per call** than in-process faster-whisper (IPC overhead);
  the model stays warm inside the sidecar between calls, so the cost is per
  request, not per chunk of audio.
- Word timestamps are Whisper-native (±100–300 ms) — no forced alignment.
  For dubbing lip-sync, use [whisperx](whisperx.md) or
  [mlx-whisper](mlx-whisper.md).
- If the sidecar dies mid-transcription the job fails with a clear
  "sidecar crashed" error and the backend stays up — retry to respawn.
- Whole-file calls share the route's duration-based deadline, including the
  Windows ROCm proxy; the child no longer stops long recordings at a separate
  ten-minute ceiling. Direct engine calls use a 600-second floor. See
  [timeout settings](../install/troubleshooting.md).
- **cuDNN 8 is still required on CUDA** — same CTranslate2 requirement as the
  in-process engine. It's checked up front so a missing cuDNN 8 shows as
  "unavailable" in Model Catalogue instead of a sidecar that
  silently fails every transcribe
  ([#1371](https://github.com/debpalash/VoiceStudio/issues/1371)).
