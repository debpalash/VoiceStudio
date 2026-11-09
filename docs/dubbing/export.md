# Exporting dubbed video

Video exports can contain both the source audio and one or more dubbed tracks.
VoiceStudio marks the selected dubbed language as the default so ordinary video
players and messaging apps play the dub immediately. Choose **Original** in the
Default Track control when the source audio should play first instead.

## Exporting before a dub exists

A transcribed video can be exported without generating a dub. SRT and VTT
export the transcript, or its translation when you have translated it, and
MP4 keeps the original audio; turn on **Burn subtitles into picture** to draw
the transcript onto the video. The editor's current lines are sent to the
backend first (`POST /dub/sync-transcript/{job_id}`), so your edits are
included. Once a dub exists, exports use the lines that dub was rendered from
and that call is refused. Audio-only projects still need a dub to export.

## Background volume

With **Background audio** on, video, WAV and MP3 exports mix the separated
background (music and ambience) under the dub. **Background volume** sets its
level from 0% to 400%; 100% keeps the original level, as earlier releases did.
A limiter stops a raised background from clipping. API clients pass
`bed_level` (0.0 to 4.0, default 1.0) to `/dub/download`, `/dub/download-audio`,
`/dub/download-mp3` and `/dub/preview-video`. The setting is desktop-only for now;
the phone apps export at the original level.

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

## Overlapping speakers in Smart Fit

Smart Fit keeps overlapping speakers on one shared video timeline. Where their
slots overlap, the largest requested video slowdown applies once; each voice
starts at its mapped source onset. Captions retain overlapping cue times, and
the background bed follows the same video timing. Non-overlapping slots keep
their existing fitting behavior.

If overlapping voices exceed the WAV level range, Smart Fit attenuates the
whole mixed track by one constant factor to avoid clipped peaks. Tracks already
within range retain their level. Legacy Stretch Video caption timing is
preserved for saved plans that place overlapping source cues sequentially.

Regenerate an affected Smart Fit track before exporting an older project:
exporting an already saved track uses its saved fit plan and audio placement.
