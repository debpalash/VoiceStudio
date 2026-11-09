# README and store media

These images use the current Electron renderer in its **default dark palette**,
with the normal Inter font, scale and appearance. The surrounding waveform
backdrop, frame and scene labels are presentation artwork; the UI inside the
frame is a browser screenshot. No app theme, layout or product defaults are
changed. The source commit, capture date, dimensions and file sizes are recorded
in [capture-manifest.json](capture-manifest.json). Development screenshots do not
identify the contents of a tagged installer.

The six PNGs are 2880 × 2000, captured at twice the display resolution. Lossless
1440 × 1000 WebP previews keep the galleries light; clicking opens the full PNG.
The four-scene GIF is 1120 × 778 at 16 fps, with soft transitions and a seamless loop.
Its linked, silent MP4 is 1440 × 1000 at 30 fps and retains more detail and color.
Readers who prefer reduced motion see the cloning still instead of the GIF.
The extra overview and model-pack images sit in a collapsed README gallery.
The same six images supply the canonical [store catalog](../../../store/assets.json).

## Demo state and privacy

The published set uses `VOICESTUDIO_CAPTURE_DEMO_READY=1`. This prepares a
**browser-only example of a studio with its primary model installed**, using the
real renderer and bundled samples. It does not download, install or load model
weights and does not show live generation or inference timings. Voice-design
description mapping runs through the real local, stateless keyword parser.

Each capture uses a fresh browser context. Only the bundled `demo0001` profile
can appear; personal profiles, projects, transcription records and export history
are excluded. Generation, downloads, profile changes and other API mutations are
blocked. The only allowed POST is the stateless description parser. Analytics
is off. The app's existing anonymous public GitHub star lookup is allowed;
other remote requests are blocked. The browser is muted.

## Regenerate

Install the repository's JavaScript/Python dependencies, Chromium, FFmpeg with
libx264, cwebp and gifsicle. Start the renderer and backend from the same checkout,
using an isolated backend data directory and an empty environment file
(`OMNIVOICE_DATA_DIR` and `OMNIVOICE_ENV_FILE`). The backend seeds the bundled
demo voice there. An empty HF cache with `HF_HUB_OFFLINE=1` is sufficient:
capture does not require installed model weights.

With the development renderer running, from the repository root:

```bash
VOICESTUDIO_CAPTURE_URL=http://localhost:3902 \
VOICESTUDIO_CAPTURE_DEMO_READY=1 \
CHROMIUM_PATH=/path/to/chromium \
node scripts/capture-readme-electron.mjs
```

`CHROMIUM_PATH` is optional when Playwright's Chromium is installed. Override
`FFMPEG_PATH`, `CWEBP_PATH` or `GIFSICLE_PATH` when those executables are not on `PATH`.
`VOICESTUDIO_CAPTURE_OUTPUT` can point at a temporary preview directory;
`VOICESTUDIO_CAPTURE_KEEP_RAW=1` retains the unframed captures for inspection.
Without `VOICESTUDIO_CAPTURE_DEMO_READY=1`, captures reflect actual backend model
readiness while retaining the same isolation, privacy filters and dark theme.

The script asserts the default dark palette and demo-only profile set, rejects
renderer errors, captures six views and encodes the GIF/MP4. It leaves normal
app preferences, profiles and models alone. Check all six images, both animation
transitions and the manifest before committing a new set.
