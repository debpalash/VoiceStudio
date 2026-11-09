# VoiceStudio — whisper.cpp Speech Recognition

Optional local transcription and translation through the upstream
[whisper.cpp CLI](https://github.com/ggml-org/whisper.cpp). It runs on the CPU
everywhere and on the GPU when your CLI build supports it: Vulkan (AMD), CUDA
(NVIDIA) or Metal (macOS). A Vulkan loader alone does not establish GPU support
in the installed CLI.

## Installing it

Binaries and models are user-managed; VoiceStudio does not download them
automatically or offer them through Model Catalogue.

1. Install or build `whisper-cli` (`whisper-cli.exe` on Windows) with the GPU
   backend you want.
2. Download a compatible Whisper GGML model, for example `ggml-base.bin`.
3. On Linux and macOS, give the CLI execute permission:
   `chmod +x /path/to/whisper-cli`.

## Selecting it

Set these environment variables before starting VoiceStudio (all desktop OSes):

- `OMNIVOICE_ASR_BACKEND=whisper-cpp` selects the engine explicitly.
- `OMNIVOICE_WHISPER_CPP_BIN` points to `whisper-cli`.
- `OMNIVOICE_WHISPER_CPP_MODEL` points to your downloaded model file.
- `OMNIVOICE_WHISPER_CPP_DEVICE` optionally selects the native GPU index.
- `OMNIVOICE_WHISPER_CPP_BACKEND=cpu` disables native GPU acceleration.

Automatic ASR selection is unchanged: whisper.cpp is used only when you pick it.

## Where it looks

- **CLI:** `backend/engines/whisper_cpp/bin/` or `PATH`;
  `OMNIVOICE_WHISPER_CPP_DIR` selects its directory. Legacy `main` executables
  are accepted only in an explicitly selected or package-local directory.
- **Models:** `backend/engines/whisper_cpp/models/`, `~/.cache/whisper/`, or the
  existing Hugging Face cache for `ggerganov/whisper.cpp`. Only a bare model
  name (such as `ggml-base.bin`) is searched there; a path in
  `OMNIVOICE_WHISPER_CPP_MODEL` that does not exist is reported as missing
  instead of loading a same-named model from another folder. This also applies
  to explicitly selected current-directory paths such as `./ggml-base.bin`
  or `.\ggml-base.bin`; use the bare name for a local cache search.

## Source language and translation

- If the CLI omits detected-language metadata, or returns malformed metadata,
  the language stays unknown instead of defaulting to English. In interactive
  dubbing, select a source language before translating. Batch dubbing needs a
  language: when ASR reports none, the job stops with a message asking you to
  pick the source language.
- The source language you choose in Dubbing applies everywhere the source text
  is read: the translation request, the Argos language packs checked and
  installed for the job, and the glossary terms extracted from it.
  Auto-detection only fills in while no explicit choice is made.
- Translation requires a multilingual model; English-only `.en`, Turbo and
  Distil checkpoints are rejected for translation. That decision follows the
  checkpoint's own file name, so a model cached in the Hugging Face hub (stored
  as a symlink into a content-addressed blob) is judged by the cached entry's
  name, not the blob's hash.

## Licences

The licence check follows the file rather than the name: a selected model keeps
the terms of the repository that physically holds it, even when a cache entry
points somewhere else. Models resolved from a Hugging Face cache retain their
source repository licence check. Other custom local files have no assumed
repository identity; their terms remain your responsibility.

## Quirks

- Global CPU selection always disables GPU use.
- Diagnostics use conservative CPU estimates because the CLI's GPU providers
  are not verified.
- Older CLI builds without full JSON output can transcribe without word timings.
- Audio conversion permits local file and pipe inputs only. Remote URL entries
  in uploaded playlists are rejected.
