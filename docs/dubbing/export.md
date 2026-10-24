# Exporting dubbed video

Video exports can contain both the source audio and one or more dubbed tracks.
VoiceStudio marks the selected dubbed language as the default so ordinary video
players and messaging apps play the dub immediately. Choose **Original** in the
Default Track control when the source audio should play first instead.

## Background volume

With **Background audio** enabled, set **Background volume** in Export options
from **0% to 400%**. **100%** keeps the existing mix level; **0%** mutes the
separated background without muting the dubbed voice. Higher values boost the
background through the existing peak limiter.

The setting is saved with the dubbing session and applies to dubbed video,
WAV and MP3 exports and the dubbed preview. Switching background audio off
exports the voice alone. The original audio track, subtitle files and exported
stems keep their existing content.

API clients can pass `bed_gain` from `0` to `4` (default `1`) to the mixed
download and preview endpoints. Invalid or non-finite values are rejected.
Different gains use separate mixed preview/audio cache entries.

## Hardsub captions

Turning on **Burn subtitles into picture (hardsub)** re-encodes the video with
captions rendered into the frames. Windows caption paths are passed to ffmpeg
with forward slashes so drive-letter paths open in the subtitles and ass
filters; POSIX filenames keep literal backslashes. Two caption styles are available:

- **Line** (default) — static line subtitles, unchanged from previous releases.
- **Karaoke (word highlight)** — each word fills with the highlight colour in
  sequence across the line. Word timings recorded at transcription time drive
  the sweep when they still spell the burned text; otherwise (older jobs, or
  translated tracks) the timing is spread evenly across each line. Karaoke is
  not available together with the dual-layout (translation + original) style —
  switching dual on falls back to the line burn.

Smart Fit exports burn captions after the video retime, so both styles follow
the fitted timeline. The karaoke script can also be downloaded on its own from
`GET /dub/ass/{job_id}` as an `.ass` sidecar.

Persisted word timings containing non-finite or unrepresentable values fall
back to even word spacing over the cue, preserving its display text. Smart Fit
drops word timings if the source/fitted spans or derived word positions are
non-finite, so invalid ASR timing cannot corrupt the karaoke script. Valid word
timing, cue positions and source records remain unchanged.
