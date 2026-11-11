# RedVoice master prompt (v01, 2026-11-11)

Paste this at the start of any Claude session that works on the RedVoice repo, or save it in the repo as `docs/MASTER-PROMPT.md` and point Claude at it.

## What RedVoice is

RedVoice is a public fork of [VoiceStudio](https://github.com/debpalash/VoiceStudio) (AGPL-3.0). It is a local, offline voice cloning and text-to-speech desktop app aimed at video editors, and it must run well on AMD Radeon GPUs, not only NVIDIA.

It is a separate version of VoiceStudio, similar but not identical, kept as an independent line of the project.

## Owner and test machine

- The owner is a video editor and motion designer (DaVinci Resolve Studio, After Effects 2026). Not a programmer: explain choices in plain language and keep steps short.
- Test PC: Windows 11 Pro, AMD Ryzen 7 7800X3D, AMD Radeon RX 9070 XT (RDNA 4, 16 GB VRAM). No NVIDIA hardware is available for testing.
- As of 2026-11-11 the PC has no git, GitHub CLI, Bun/Node or real Python (only the Store stub). The owner agreed to install them with winget after 2026-11-16. Until then, work happens in the GitHub repo only and nothing can be run on the GPU.

## Decisions already made

| Topic | Decision |
|---|---|
| Code base | Reuse VoiceStudio source (fork), do not rewrite from scratch |
| Repo | Public GitHub fork named `RedVoice` |
| License | Stays AGPL-3.0-only (required by the fork) |
| Platform focus | Windows desktop app first. Website version later |
| v1 feature | Voice cloning + TTS |
| Language | English only in v1 |
| Other VoiceStudio features (dubbing, dictation, audiobooks, mobile) | Keep the code, hide them in the UI behind a setting or flag |
| Online parts (Pro, Cloud, accounts, update checks) | Disabled. Fully offline; only model downloads use the internet, and only after asking |
| Look | Dark, like Resolve: neutral dark panels, red accent |
| Speed vs quality | Quality first by default, plus a faster draft mode |
| Name | RedVoice |

## What makes RedVoice different

1. **AMD first.** The app detects the GPU vendor and picks a working, accelerated path on Radeon without manual setup.
2. **Script to per-line takes.** Paste a script; each line becomes its own clip with several takes to choose from. Files are clearly named (`line-03_take-02.wav` style) and regenerating one line does not touch the others.
3. **Voice timed to subtitles.** Import an SRT; generate speech for each subtitle that fits its time slot, and export clips plus a matching SRT.
4. **Send VO to Resolve timeline.** One click places the chosen takes on an audio track in the open DaVinci Resolve Studio project, at the right timecodes, using Resolve's Python scripting API.

After Effects hand-off was considered and left out of v1.

## AMD work: starting facts and what to verify

Known from the VoiceStudio repo on 2026-11-11:

- `pyproject.toml` pulls PyTorch from the CUDA `cu128` index by default on Windows and Linux, and falls back to CPU when no NVIDIA driver is found.
- An experimental Windows ROCm setup exists (`docs/install/windows-rocm.md`) but lists only RX 6000 cards (gfx1030/1031/1032). The RX 9070 XT is not covered.
- The native GGUF engines (OmniVoice GGUF, CosyVoice GGUF) support Vulkan on AMD, Intel and NVIDIA.
- Some small engines run on ONNX (KittenTTS, Supertonic-3, sherpa-onnx on CPU).

Not yet verified, check against current documentation before choosing:

- Whether AMD's PyTorch-on-Windows ROCm builds support the RX 9070 XT, which versions, and whether they are compatible with the torch version the repo pins.
- Real speed and quality of the Vulkan GGUF path on the RX 9070 XT compared with CPU.
- Whether ONNX Runtime with DirectML is a useful path for the ONNX engines.

Planned order: first make the Vulkan GGUF path the automatic default on AMD cards (lowest risk, already in the code), measure it, then investigate ROCm for the PyTorch engines. Do not claim an AMD path works until it has been run on the test PC.

## Rules for working on this repo

- Read the repo's own `CLAUDE.md` and `AGENTS.md` first and follow their conventions.
- Keep the AGPL-3.0 license, all existing copyright notices and `LICENSE-NOTICE.md`. State in the README that RedVoice is a modified fork of VoiceStudio and summarize what changed.
- Rename user-facing branding to RedVoice (app name, window title, icons, installer names). Do not use the VoiceStudio name or logo as RedVoice's own branding.
- Remove or disable calls to VoiceStudio servers: installer scripts, update checks, Pro/Cloud/account endpoints, telemetry if any.
- Hidden features stay in the code and should remain buildable, so upstream changes can still be merged. Prefer small, isolated changes over large rewrites for the same reason.
- NVIDIA and CPU support must keep working; AMD is added, nothing is taken away.
- Everything stays editable and clearly named.
- Report honestly: if something was not tested on real hardware, say so.

## Milestones

1. **Repo setup**: fork exists, this prompt is in `docs/`, README states the fork relationship.
2. **Rebrand and trim the UI**: RedVoice name and dark red theme, non-v1 features hidden, online parts disabled.
3. **AMD path**: GPU vendor detection, automatic Vulkan GGUF default on Radeon, ROCm investigation, results measured on the RX 9070 XT (needs the test PC, after 2026-11-16).
4. **Script to per-line takes.**
5. **Voice timed to subtitles (SRT).**
6. **Send VO to Resolve timeline.**
7. **Windows installer build** for RedVoice.

Milestones 1, 2 and the code for 4 to 6 can be done from the web. Milestone 3 testing and any real audio check need the PC.

## Open questions

- Which engine is the quality-first default for English cloning on AMD (decide after measuring in milestone 3).
- Should RedVoice keep pulling updates from VoiceStudio regularly, or drift apart over time?
- Website version: scope and hosting, to be discussed after the desktop app works.
