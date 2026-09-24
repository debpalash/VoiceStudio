# VoiceStudio — OmniVoice Engine (default)

OmniVoice (k2-fsa/OmniVoice) is VoiceStudio's default TTS engine — the one a
fresh install uses without any configuration. It does zero-shot voice cloning
across 600+ languages and outputs 24 kHz mono audio. Voice cloning, dubbing,
and dictation all run on it out of the box.

## When to pick it

- You want cloning plus the broadest language coverage (see
  [languages.md](../languages.md)).
- You have a GPU (CUDA, AMD ROCm on Linux, or Apple Silicon MPS) with
  ~6 GB VRAM or more.
- You just installed VoiceStudio — it's already selected.

For low-VRAM or CPU-only machines, the
[OmniVoice GGUF](omnivoice-gguf.md) variant runs the same model through a
quantized native binary with a much smaller memory footprint.

## Requirements

- Runs on CUDA, AMD ROCm (Linux), MPS (Apple Silicon), or CPU — auto-detected.
- Recommended VRAM floor: **6 GB** on a dedicated GPU. This is the only
  engine with a measured floor: on 4 GB cards (GTX 1650 Ti, Quadro P2000 —
  issues [#1226](https://github.com/debpalash/VoiceStudio/issues/1226) /
  [#1222](https://github.com/debpalash/VoiceStudio/issues/1222)) the driver
  pages to system RAM and a render that should take seconds runs for minutes
  until the compute budget kills it. The UI warns before you wait; nothing
  hard-blocks, since short inputs can still fit. A CUDA or ROCm card below the
  floor is also budgeted as the CPU-class hardware it performs like — the longer
  `OMNIVOICE_CPU_GENERATE_TIMEOUT_S` (600 s), not the accelerated 300 s
  ([#1804](https://github.com/debpalash/VoiceStudio/issues/1804)). Apple Silicon
  is excluded: unified memory has no dedicated pool to compare against.
- No extra install — the model ships with the app and downloads its weights
  on first use (see [downloading-models.md](../downloading-models.md)).

## Selecting the engine

OmniVoice is the default, so normally there is nothing to do. If you switched
away and want it back:

- **Model Catalogue**, or
- set `OMNIVOICE_TTS_BACKEND=omnivoice`.

The env var overrides the persisted UI choice.

## Behaviour notes

- Weights load lazily on first use and are shared with the rest of the app
  (dubbing, dictation) — the model is never double-loaded.
- On CUDA and ROCm the model runs fp16 with `torch.compile`; PyTorch exposes
  ROCm/HIP devices through its `cuda` API, while VoiceStudio's engine matrix
  reports the hardware as ROCm. An installed speech recognizer can supply
  missing reference transcripts for cloning.
- Output is 24 kHz mono; the shared mastering chain (highpass + compressor)
  is tuned for this rate and applied automatically.
- Cloning takes a short reference clip (`ref_audio`); 3–10 seconds is the
  sweet spot. A transcript of the clip improves conditioning — if the profile
  has none, VoiceStudio uses an already-installed speech recognizer on first
  use and saves the result to the profile. This includes the catalogue’s
  Whisper Turbo CT2 build; cloning does not require a second Transformers copy.
  The model-level Whisper fallback
  also requires cached weights; it never downloads another ASR during cloning.
  If no recognizer is installed, supply a matching reference transcript or
  explicitly install and select a speech-to-text model in Model Catalogue. A clip with a supplied transcript is limited
  to 20 seconds so the two stay aligned; trim both to the same passage. Without
  a transcript, VoiceStudio can search up to 75 seconds in five contiguous,
  bounded transcription passes and selects the 15-second passage with the most
  detected speech. Those passes use the speech-to-text model already installed
  in Model Catalogue. For a clip longer than 20 seconds, VoiceStudio skips
  whole-clip transcription and ignores a saved profile transcript, so long
  saved voices use this selection too. OmniVoice's own Whisper snapshot is only
  a fallback when no catalogue recognizer can transcribe a window, and it is
  never downloaded during cloning. A transcript typed on the request for
  such a clip is rejected with `[clone_ref_too_long]`.
  Only that 15-second window is sent to the model. Clips longer than 75 seconds
  must be trimmed first. If no spoken words are detected, trim to
  a clear 3–10 second passage or provide its matching transcript.
- For cross-language cloning, keep the reference transcript in the sample's
  original language and write the new script in the desired output language.
  Choose that output language, or **Auto** for language-agnostic synthesis.
  Choosing/uploading a different reference voice preserves the script's language
  setting. The language picker guides synthesis; it does not translate text.
  In `POST /generate` and shared profile conditioning, an explicit `language=Auto`
  overrides the saved profile language; omitting `language` retains the profile
  default for existing API clients. Engine language coverage and accent quality
  still vary; use a multilingual engine that supports the target language.
- Encoded voice references persist on disk (`prompt_cache/` in the app data
  dir), so known short references skip re-encoding and transcription after a
  restart. Long references must rank windows once after a restart to recover
  the selected transcript; subsequent generations reuse the chosen passage
  and cached prompt without re-decoding the clip. Set
  `OMNIVOICE_PROMPT_DISK_CACHE=0` to keep the prompt cache in memory only.
- Style attributes (`instruct`) and a reference clip can be **combined**:
  when they agree, the instruct stabilizes cloning for the attributes it
  names (upstream documents dialect cloning as the canonical case — dialect
  reference + matching dialect instruct). When they conflict, the reference
  audio wins.
- Inline pronunciation control: Chinese via pinyin with tone numbers
  (`打ZHE2出售`), English via bracketed CMU phonemes (`[B EY1 S]`). Non-verbal
  tags like `[laughter]` are covered in
  [expressive-speech.md](../expressive-speech.md).
- Voice design works from attributes (gender, age, pitch, whisper, English
  accents, Chinese dialects) via the Design tab — no reference audio needed.
- Optional FlashInfer acceleration on CUDA: set `OMNIVOICE_FLASHINFER=1`
  (or `=graph` for CUDA-graph capture, best for one render at a time) after
  installing the `flashinfer-python` package — see
  [performance.md](../performance.md). Off by default; if the package is
  missing or a kernel fails, the app logs why and continues on the standard
  path.

## Known limits

- Voice design understands only the fixed attribute vocabulary — free-form
  design *prose* is mapped onto those attributes, and wording outside them
  is ignored. Design is trained on English and Chinese and can be unstable
  in low-resource languages; for description-driven design in other cases
  try [VoxCPM2](voxcpm2.md).
- Below the 6 GB VRAM floor on a CUDA or ROCm card, expect very slow renders;
  they get the longer CPU compute-time budget rather than the accelerated one,
  but can still time out. Prefer [OmniVoice GGUF](omnivoice-gguf.md) or a CPU
  engine such as [PocketTTS](pockettts.md).

## Troubleshooting

- "Too heavy for the available compute" on a small GPU: see the VRAM floor
  above — switch to OmniVoice GGUF or close other GPU apps.
- First generation is slow: the first call downloads multi-GB weights. To
  keep the first render quick, install the model ahead of time from
  **Model Catalogue (TTS tab → the engine's Weights)** — a long first generate is almost always the
  download, not a hang.
- General install issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[performance.md](../performance.md),
[expressive-speech.md](../expressive-speech.md),
[disk usage](disk-usage.md).

A timed-out subprocess is killed and given a bounded wait to exit before the
request returns, so retrying cannot reuse its closing process. Timeout cleanup
remains tied to the original child and cannot kill a replacement sidecar.
If that wait cannot confirm exit, VoiceStudio retains the process for cleanup
and blocks another attempt until it can be reaped, rather than starting a second
engine alongside it. A later retry or shutdown retries the bounded cleanup.
