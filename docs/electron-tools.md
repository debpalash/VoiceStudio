# Electron tools workspace

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Tools in the cloning sidebar or command search opens three utility panes:

- Directorial AI parses direction into instruction, translation hint, rate bias,
  tokens and taxonomy using the existing direction service.
- Speech-rate fit submits translated text, a positive time slot and target
  language to the existing fitting service. Invalid durations block submission.
- Probe file submits an absolute path to the existing ffprobe metadata endpoint.

Each operation starts only on explicit submission. Switching tools aborts the
frontend request and prevents stale results from replacing another tool's content.
LLM-backed operations follow the configured backend routing. Errors stay compact
with expandable diagnostics. No new processing engine or network dependency is added.

The tools browser smoke verifies request fields and results with mocked responses.
Settings > Audio tools exposes the existing app-managed media bundle installer,
status/progress, system-copy selection, restore, and yt-dlp update/restore actions.
Downloads start only through an explicit action. Custom executable selection still
needs the Electron native path-authorization bridge.

The existing checksummed installer successfully installed ffprobe in the development
app data directory. A real probe then read two streams and a four-second duration
from an MP4 fixture. No system-wide binary or application version was changed.

Custom FFmpeg/FFprobe executables can be selected from Media tools in the desktop app. The native picker validates the selected executable with `-version`, then issues the same one-use path capability used by Tauri in the backend-advertised data directory. Cancelling leaves the current tool unchanged. Browser-only sessions do not expose the native picker.

The live Windows conversion smoke now exercises the complete Tools > Convert path with the selected
Faster-Whisper and OmniVoice engines: upload, ASR, saved-profile conversion, returned waveform and
Vidstack playback-clock advancement. The fixture uses an existing clone profile and does not change
the user's engine selections.

Tools > Convert lists saved Voice Design profiles as well as clones with reference
audio or a locked take. Search by the saved profile name, select it, and upload or
record the source clip. Designed voices use their saved sample and instructions;
legacy designs without a sample fall back to their saved instructions and seed.
A ready TTS engine with cloning support is still required. Conversion transcribes
the source and synthesizes its text in the selected voice. Match duration adjusts
the overall length; it does not preserve the original performance's pitch, pauses,
or expression. The result has the same Download action as Clone and Design takes:
in the desktop app it opens the native save dialog for the WAV file and records the
file in export history. The result is also saved to History.

**Keep my delivery** (#3421) converts the recording itself instead: only the voice
changes, and the source's timing, pauses, rhythm and intonation stay as recorded.
Nothing is transcribed, so source language and Match duration do not apply. It
needs CosyVoice 3 installed (Model Catalogue → Engines); it does not have to be the
active engine. The target voice needs a reference recording: a clone, or a design
with a saved sample. Clips longer than about 28 seconds are converted in pieces
split at pauses; sources over 10 minutes are refused. Voices without a recording
(designs with no saved sample) are hidden while it is on. The default (off) keeps the transcribe-and-re-speak behaviour.

Media-tool installation retries temporary filesystem locks for up to four seconds,
including Windows locks after the executable probe. It preserves the existing installation in a sibling backup
until publication succeeds and attempts to restore it if publication fails. If
rollback also fails, the previous installation remains in `.media-backup-*/previous`;
the error includes that recovery path. Persistent locks surface their original
error; no system binaries or user media are deleted. The same behavior applies to
yt-dlp overlay updates, and restoring the tested version reports failed cleanup.
Dubbing background preservation and long export filter graphs support both legacy
`-filter_complex_script` and modern FFmpeg `-/filter_complex` file arguments.
If FFmpeg rejects the legacy option before processing, VoiceStudio retries once
with the modern spelling; filter contents stay in a file to avoid Windows command
line limits. Actual decoding/filter errors are reported without retrying.
