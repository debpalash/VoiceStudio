# VoiceStudio — Speaker Diarization

> Native Windows ROCm changes on this branch are **DRAFT / not fully validated**.
> Earlier hardware checks do not certify this selective integration; full
> WhisperX remains unavailable. See the [draft scope and path limitations](../install/windows-rocm.md).

Diarization splits a single audio stream into per-speaker tracks: who said
what, and when. VoiceStudio uses **pyannote** + **WhisperX** under the hood —
the same stack the original WhisperX paper used.

## What diarization buys you

- Multi-speaker dubbing: each detected speaker gets its own voice clone in
  the target language.
- Subtitle styling: speaker labels (`SPEAKER_00:`, `SPEAKER_01:`, …) on the
  exported SRT/VTT files.
- Audio editing: per-speaker tracks in the timeline view.

## License acceptance flow

The diarization model — `pyannote/speaker-diarization-3.1` — is **gated** on
HuggingFace. A valid HF token alone is not enough: you also need to accept
the model's license once.

1. Get a HF token if you don't have one — see
   [docs/setup/huggingface-token.md](../setup/huggingface-token.md).
2. Set the token via **Settings → API Keys** (or any of the other supported
   paths).
3. While signed in to HuggingFace with the same account, visit:
   - https://huggingface.co/pyannote/speaker-diarization-3.1 → **"Agree and
     access repository"**.
   - https://huggingface.co/pyannote/segmentation-3.0 → same.
4. Install the model in **Settings > Models > Diarisation**. Wait for installation
   to finish, then retry transcription. Jobs only load locally installed files.

If you skip the license acceptance, the HF API returns `401 Unauthorized` or `403 Forbidden` for
the download — the same error class the in-app **"Open docs for this error"**
button deeplinks to.

## Fallback behaviour

When diarization files are missing or the installed runtime cannot load,
VoiceStudio falls back to a **silence-gap heuristic**. The job warning explains
whether to install/repair the model or inspect the backend log. Jobs never
download missing weights.

The heuristic is not as accurate as pyannote — speakers with similar pitch
or rapid turn-taking conversation get merged — but it lets the dub finish
end-to-end instead of erroring.

## HF token requirement

An HF token and accepted repository access are required to install the gated
pyannote bundle. Once its pipeline, segmentation and embedding files are cached,
inference works offline without retaining the token. See
[Hugging Face token setup](../setup/huggingface-token.md).

## Troubleshooting

- Install returns 401/403: verify that the token belongs to the account with
  access to both gated repositories.
- Missing files: install or repair from Settings > Models > Diarisation.
- Installed model fails to load: inspect Settings > Logs > Backend. A runtime
  failure does not by itself indicate a licence or token problem.

## Local installation and repair

Install pyannote from Settings > Models > Diarisation after completing the access steps above. The install includes its segmentation and speaker-embedding checkpoints. A pipeline configuration alone is not a complete installation; repair also retrieves missing dependencies.

Dubbing resolves all three files from the local cache and does not download models during a job. Once installed, the bundle can run without retaining an HF token. Missing files prompt installation or repair; transcription continues with the existing silence-gap fallback and its accuracy caveat. The local bundle is checked before importing the optional runtime, so missing models or a missing installation token are not disguised by a dependency import error. A runtime load failure is reported separately from missing files.

## Native Sortformer adapter

The shared diarisation path also supports audio.cpp Sortformer v1. Install the reviewed model and its checksummed native runtime in **Settings → Models → Diarisation**, then select Sortformer on that page. VoiceStudio resolves the installed Q8 GGUF locally and persists the choice; it never downloads a model while transcribing. `OMNIVOICE_DIARIZATION_BACKEND` and `OMNIVOICE_DIARIZATION_MODEL` remain authoritative overrides for managed deployments.

The adapter normalizes input to mono 16 kHz, converts sample-based turns to the shared annotation format, and contains the native process with a ten-minute timeout. Sortformer supports up to four speakers but cannot enforce the existing exact-speaker-count setting; requesting that setting follows the existing fallback/warning path. Its accelerator graph grows from the default 20-second context for clips up to 120 seconds. Longer recordings require pyannote until locally converted Sortformer v2.1 streaming weights can be distributed. No binary or model is downloaded implicitly. The model's CC-BY-NC-4.0 restrictions apply; see the upstream audio.cpp model card. Native process status and cancellation are wired into the shared job lifecycle.

Run `uv run --no-sync python scripts/smoke_sortformer.py <local-media> --min-speakers 2`
to verify an installed runtime against local media. Add `--seconds 120 --cancel-after 0.5`
to verify process cancellation. The smoke writes only a normalized temporary clip and never
downloads models or changes app data. On the maintained Windows RTX 4090 fixture, v0.7.4
processed a real 60-second Dubbing source through Vulkan in 3.27 seconds, returned 16 bounded
turns, and cancelled the 120-second variant in 1.2 seconds without leaving a registered process.

On Windows with an RX 9070 XT (AMD driver 26.10.44), the pinned v0.7.4 Vulkan
release lists the card as `Vulkan:0`. With the release and Q8 GGUF in a local
diagnostic directory, the adapter selects `--backend vulkan --device 0` in
auto mode and when `OMNIVOICE_DEVICE=rocm` is requested; this is Vulkan on AMD,
not the HIP backend. A 24.72-second clip assembled from two bundled voice
samples yielded the same two-speaker turns on Vulkan and CPU. Repeating a
bundled speech sample yielded bounded turns at 60 and 120 seconds; cancellation
of the 120-second run left no native process registered. These checks confirm
native inference and routing, not an end-to-end Settings installation, a
head-to-head accuracy comparison with the RTX fixture, or a pyannote fix.

audio.cpp v0.7.4 contains the newer Sortformer v2.1 streaming runtime. Its NVIDIA Open Model License checkpoint currently has no redistributable GGUF package, so VoiceStudio does not advertise a download that upstream cannot legally supply. A locally converted mixed F16/F32 checkpoint is the planned long-recording path once the model can be offered through an explicit local-package workflow.
