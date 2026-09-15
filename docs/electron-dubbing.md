# Electron dubbing workspace

The idle workspace includes an original/dubbed demo comparison with compact
player controls. Sync playheads aligns positions without starting both videos.
Sample transcript edits are retained per language while the demo is mounted;
they do not regenerate the prerecorded audio. Edit on the dubbed card imports
that sample video into the normal upload/transcription and editing workflow.

Open Dub from the cloning sidebar or command search. Upload or drop audio/video, or explicitly submit a video URL;
preparation completes before transcription starts. The editor shows source text,
editable translated text, and per-segment voice/timing controls. Translation uses
the selected Settings > Models > Translation provider. Choose a target language,
translate, review the text, then generate. Completed tracks can be previewed and
exported through the native save dialog.

Segment rows scan as compact source/translation pairs: speaker, voice, fit state,
selection and timestamp stay visible, while row actions reveal on hover or keyboard
focus. Inset hairline separators preserve the reading rhythm; source text and metadata
stay dimmed until the row is active, while the translation remains the visual lead.
Clicking a translation turns only that row into a growing editor. Advanced
voice and timing controls remain folded behind the speaker header. When a workspace
also opens local controls, constrained or scaled windows automatically use the main
navigation rail so the transcript keeps the available width; expanding it remains an
explicit temporary override.
When several targets are selected, progress tabs above the transcript switch the
active language in one click while preserving every target for Translate All and
Generate. Persisted provider error pages are discarded and restored to the source
dialogue with a retryable error state.

Long projects virtualize transcript rows, so only the visible editors are mounted.
Timeline waveform peaks and onsets are computed once by the backend and cached as a
small JSON payload; Chromium never decodes the full separated-vocals WAV to draw the
timeline. Video previews are written atomically with MP4 fast-start metadata and are
immutable per generated-track revision. The renderer warms the current target in the
background, reuses one Vidstack player while switching tracks, preserves the playhead
and playing state across Original/Dub changes, and uses byte ranges on subsequent
playback. Extension-derived native MIME hints select Vidstack's native provider
immediately for common MP4, WebM, Ogg, MOV and MKV sources; URL imports use the
backend's normalized MP4 type even when their display name has no extension. The media endpoints
also answer metadata-only `HEAD` requests, including after backend restart, without
reading the source body or starting a preview mux. Local NLLB batches scale with available accelerator memory while
bounding batch multiplied by beam count.

The backend remains responsible for separation, ASR, speaker cloning, translation,
TTS, fitting, mixing and export. Electron reuses Tauri's speaker binding and
segment generation helpers. A stream close without a terminal event is a failure,
not success. Cancellation aborts the HTTP stream and requests backend task/job
cancellation. Edits, target language, track metadata and task IDs persist locally across reloads.
Interrupted preparation/generation offers Resume, which reads the existing task
and replays its stream; generation is never resubmitted just because the UI reloaded.
Interrupted transcription offers an explicit Retry against the existing prepared
media, without uploading or preparing the source again. ASR restarts from the
beginning because its backend stream is request-scoped, not a replayable task. Batch language runs and advanced QC controls remain in `electron/PARITY.md`.

Verification: `node electron/tests/dub-smoke.mjs` against the development renderer.
The test mocks backend jobs and never uploads or generates user media. Optionally
set `VOICESTUDIO_TEST_VIDEO` to a local MP4 fixture to verify native video transport.
These checks establish UI wiring, not a completed real model-backed dubbing run.
`node electron/tests/native-translation-agent-smoke.mjs <agent>` separately launches the packaged
app with isolated data and verifies that the detected CLI returns complete, ordered translations
for real time-budgeted segments without a backend or source checkout. Codex, Claude Code and
OpenCode pass on the current Windows host; Pi remains gated on a host where it is installed.
`uv run python scripts/smoke_dub_url_captions.py` separately verifies the live
public downloader without loading speech models or touching app data. The current
smoke downloaded a browser-safe MP4 and one original-language caption track, then
parsed 165 usable cues.

A disconnected preparation or generation stream retains its existing task for Resume or Cancel. Editing and new jobs remain disabled until that task finishes or cancellation is confirmed; reconnecting never creates a replacement generation. A task already absent from the backend counts as cancelled. If the backend cannot confirm cancellation, Change file explicitly abandons the unreachable local recovery record so the workspace cannot become permanently blocked.

The setup sidebar groups the source, target language and translation engine, timing,
production overrides and export choices into stable sections. Advanced controls stay
collapsed until requested. Before media is loaded, the main workspace presents the
three actual steps—upload and transcribe, translate, generate—and hides inactive
generation actions.

Import .srt replaces the current segment text and timings after source preparation.
The backend retains voice references only where their timing overlaps the new cues.
Malformed, overlapping, or duration-clamped cue counts remain visible in the sidebar.
Failed imports preserve the current edits. Generated track buttons clear on successful
replacement to avoid presenting older audio as the new subtitles' output.
URL import runs only after clicking Ingest; it uses the backend's existing yt-dlp
pipeline. Explicit cookies.txt selection is available under URL sign-in options; optional caption downloads are available.

Translation quality uses the existing backend Fast, Autofit and Cinematic modes.
The choice persists in the working draft and saved project (`translateQuality`),
including legacy project imports. New media preserves the user's quality choice.
If the backend reports that no LLM is configured, the UI selects Fast and shows
an inline explanation with a link to LLM settings. It does not silently claim
that Cinematic or Autofit completed. Browser fixtures cover this fallback and
reload persistence; real LLM-backed quality passes remain unverified.

**Translate with Agent** detects the installed Codex, Claude Code, OpenCode and Pi CLIs. Dubbing
presents Agent and the active Google, Argos, NLLB or API engine as separate translator choices;
Fast, Autofit and Cinematic remain quality choices for engine translation. The selected agent receives the complete ordered
dialogue, glossary, dialect and per-segment speech budget as untrusted data, returns a strict
id-preserving translation, and never receives repository or filesystem write access. Google,
Argos, NLLB and configured API translators remain available through the ordinary Translate All
action. Every translate entry point honors the chosen translator. Agent translations use the same reviewable segment fields and, during generation, reuse the
bounded measured-speech loop to rewrite only timing misses before rerendering. Timing-fit provenance
is stored per target language, so every translated track keeps that behavior after language switches
and draft reloads. Cancelling Dubbing also terminates the local agent process tree.

Export options expand inside the existing sidebar. Users can select included video
tracks and the default track, background mixing, burned subtitles, dual layout and
karaoke (disabled with dual layout). Audio supports WAV or MP3 with bitrate choice;
SRT/VTT/ASS sidecars and per-language stem/segment ZIPs use the existing backend.
Each download is explicit and targets the selected language. Export errors retain
all choices for retry. Native save filters match the encoded file format.
Browser fixtures verify MP3/SRT downloads, query options and failed-export retry;
unit tests cover video/package parameters. Real rendered exports, batch presets
and native save dialogs remain unverified or incomplete.

Timing options now match Tauri: Concise, Smart Fit, Stretch Video and Lip sync.
Voice matching offers per-line references or one consistent reference per speaker.
These choices persist in working drafts and projects and are sent to generation;
existing defaults remain Strict slot and Per line. Interrupted generation retains
its submitted timing strategy. Completed output retains that strategy separately
from current controls, so changing the next render's settings does not change the
export capability of existing audio. Stretch Video output disables incompatible
subtitle burn-in and directs users to sidecar exports instead. Browser fixtures
verify persisted settings, request values and the export guard; actual timing and
voice consistency across a real multi-speaker render remain unverified.

ASR retry browser coverage includes stream interruption, reload with zero automatic
requests and successful explicit retry. Cancel releases recovery only after the
backend acknowledges it. A real mid-ASR disconnect remains unverified.

Spoken-language and speaker-count hints are available in a collapsed sidebar
section. Automatic detection remains the default. Explicit language hints reach
both local uploads and URL ingest; speaker counts (1?20) reach transcription and
persist through retries, drafts and projects. Browser checks exercise selection,
reload and retry; unit tests inspect both file and URL request bodies. These are
hints to the existing backend, not a guarantee of diarization accuracy.

Cookie exports are selected explicitly for one import, limited to 1 MB and sent
only over HTTPS or the local desktop transport. Selection clears on submission;
contents never enter the persisted dubbing session. Tauri and Electron share size
and transport validation constants/helpers. Browser fixtures verify oversized
rejection, request contents and absence from local storage; the Tauri cookie tests
remain green. No real authenticated website download was performed.

URL Advanced options include downloading available captions through the existing
yt-dlp pipeline. When usable cues are returned, Electron chooses the closest
source-language track, normalizes its timing and opens it directly in the editor.
Missing or malformed tracks fall back to the normal ASR path without another user
decision. The downloader skips automatic translations. Real caption downloads
are verified by the isolated public-URL smoke above. Authenticated sites still
require a user-owned cookies export for native acceptance.

Production overrides expose steps, guidance, speed and global voice direction,
matching the existing Tauri generation request. Defaults remain 16 / 2 / 1 with
no direction. Values persist in drafts and projects; Reset clears only these
overrides. Browser checks verify reload followed by the exact generation values,
including zero guidance. Engine-specific audible effects remain unverified.

Saved Smart Fit override values now survive project import and draft recovery and
reach generation only when Smart Fit is selected. Other timing strategies omit
those parameters. Unit tests cover round-trip preservation and request routing;
there is no new tuning panel (Tauri exposes these as stored preferences).

Real export verification: `tests/test_smart_fit_export.py` passes all 43 tests
with the installed app-managed FFmpeg/ffprobe explicitly supplied. Its seven
integration cases render synthetic video/audio through retiming and the backend
export endpoint, then probe output durations and fitted subtitle bounds. This
proves those backend export paths with real codecs; it does not prove a complete
Electron upload ? ASR ? multi-speaker synthesis run. The broader targeted export
suite passed 73 tests before the codec paths were supplied (seven skipped then).

Export format, bitrate, track selection, background mixing and subtitle choices now
persist in the working draft and saved project. Legacy Tauri export preferences
populate the panel rather than merely being retained as unused data. Browser
checks verify format/bitrate/background/dual-layout recovery after reload; project
tests verify legacy disabled tracks and background preferences.

Each segment now exposes volume (0?2), optional speed and a direction note within
its existing voice disclosure. Empty speed follows global speed. Segment gain
reaches generation separately from shared TTS fingerprint inputs, matching Tauri;
zero is preserved for muted segments. Browser checks cover edits, reload and
request values; backend audible mixing is not established by these UI fixtures.

Segment rows support insertion, deletion, cursor-aware splitting and merging with
either neighbor. Merge/split uses Tauri's shared attribution bookkeeping, so speaker,
voice, direction, gain and target language survive moving words across a boundary.
Undo and redo retain the latest 50 edit states and reset when a new source, subtitle
file, translation or project becomes the editing baseline. The toolbar and
Cmd/Ctrl shortcuts expose the same operations. Unit and browser regressions cover
the full insert/delete/merge/split/undo/redo sequence and merge-to-split attribution.

The editor includes a proportional timing lane above the segment rows. Segment
blocks select their matching row, drag to move, expose edge handles for resizing,
and support keyboard nudging or deletion. Timing changes use Tauri's shared clamp
and speed-recalculation helpers. Adjacent overlaps are highlighted in the lane with
an explicit warning; the media duration reported by preparation defines its scale.
Browser coverage verifies timeline rendering, keyboard timing edits and overlap QC.

Segment checkboxes now enable one bulk edit surface without changing the active
timeline segment. A single operation applies a saved voice, per-segment target
language reset/override, or deletion to the selection and records one undo step.
Select all and Clear keep large transcripts manageable while generation is idle.

Dubbing uses a wider responsive secondary sidebar (up to 32rem) and a wider transcript workspace. Once media is prepared, the upload form becomes a compact source row; source, target and casting remain a visible three-step sequence. Automatic cast matching is the default, so its overrides start folded. Voice chips wrap instead of requiring horizontal scrolling. Segment rows show the original text only when it differs from the editable text, retaining source comparison after translation without duplicating every unmodified transcript.

Below 40rem of workspace width, Dubbing stacks its controls above the editor with controls limited to 40% of the available height. Both regions retain independent scrolling.

Global speed/quality changes preserve explicit Dubbing production steps, including settings restored from projects. Unset steps continue to follow the backend's current preset.

NLLB resolves explicit FLORES language/script codes, unambiguous ISO-639-3 codes and common short aliases, including Traditional Chinese. Unsupported or script-ambiguous source, target or per-segment languages are rejected before model loading instead of silently translating to English.
