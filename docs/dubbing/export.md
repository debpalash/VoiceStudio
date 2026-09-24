# Exporting dubbed video

Video exports can contain both the source audio and one or more dubbed tracks.
VoiceStudio marks the selected dubbed language as the default so ordinary video
players and messaging apps play the dub immediately. Choose **Original** in the
Default Track control when the source audio should play first instead.

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
