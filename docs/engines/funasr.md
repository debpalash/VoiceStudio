# VoiceStudio — FunASR (SenseVoice) Engine

FunASR drives Alibaba's SenseVoiceSmall with FSMN-VAD: an all-in-one
multilingual pipeline — transcription with punctuation and inverse text
normalization across **50+ languages**, plus optional **inline speaker
diarization** via the cam++ speaker model. It's the opt-in alternative to
WhisperX ([#182](https://github.com/debpalash/VoiceStudio/issues/182));
WhisperX remains the cross-platform default.

## Selecting it

- Install it into the app venv: `uv pip install funasr`.
- Then **Settings → Models**, ASR tab → **Use** on the FunASR row, or
  `OMNIVOICE_ASR_BACKEND=funasr`.

Auto-detect never picks it; it's an explicit opt-in.

## Best at

- **Multi-speaker transcription without any HuggingFace token.** This is the
  only ASR engine with diarization built in: cam++ labels each sentence
  (`Speaker 1`, `Speaker 2`, ...) in the same pass — no gated pyannote
  model, no license click-through. Compare
  [diarization](../features/diarization.md) for the pyannote/WhisperX route
  and what each buys you.
- **Broad language coverage** beyond Whisper's strongest languages, with
  punctuation included.

## Not suited for

- **Lip-sync dubbing** — FunASR returns sentence-level timestamps, not
  word-level ones. Use [whisperx](whisperx.md) /
  [mlx-whisper](mlx-whisper.md) when word timing matters.

## Platform support

CUDA or CPU, on macOS, Windows, and Linux.

## Model selection

| Variable | Default | Role |
| --- | --- | --- |
| `ASR_MODEL_FUNASR` | `iic/SenseVoiceSmall` | main ASR model |
| `ASR_FUNASR_VAD` | `fsmn-vad` | VAD segmentation model |
| `ASR_FUNASR_SPK` | `cam++` | speaker model; set to empty (`ASR_FUNASR_SPK=`) to disable diarization and use the dub pipeline's pyannote/heuristic path instead |

Weights download on first load (through FunASR's own model hub) — see
[downloading-models](../downloading-models.md).

## Quirks

- With the speaker model enabled, long recordings are transcribed in **one
  call** and split by FunASR's internal VAD — cam++ assigns speaker cluster
  IDs per call, so this is what keeps "Speaker 1" meaning the same person
  across the whole file.
- The engine runs with `spk_mode="vad_segment"`: FunASR 1.3.1's default
  (`punc_segment`) requires a separate punctuation model and crashes when
  SenseVoice is loaded without one.
- SenseVoice's rich-token markup (language/emotion/event tags around the
  text) is stripped from the output automatically.
- Language detection is automatic (`language: auto`); the detected language
  is reported per file.
