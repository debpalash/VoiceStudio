# Electron playback

Existing Electron preview, reference, history and generated-output playback use
Vidstack through `components/media-player.tsx`. Generation stores the result and
its autoplay intent; only the visible output player starts playback. The shared
playback owner pauses another preview before taking ownership.

WaveSurfer uses the Vidstack-owned audio element for waveform visualization.
It does not own a second player. Blob and extensionless API sources explicitly
use native MIME sniffing through `audioSource`; otherwise Vidstack infers blobs
as video. Seek input processes the input event once, avoiding a second change
event restoring the previous controlled value before the provider reports seeked.

Run `node electron/tests/playback-smoke.mjs` with the development renderer running.
The test uses synthetic WAV/profile/generation responses and never saves user data.
Set `PLAYWRIGHT_CHANNEL` or `VOICESTUDIO_UI_URL` for another installed browser/server.

HLS and DASH libraries are bundled and lazy-loaded. The shared provider includes
Vidstack's native audio/video, HLS, DASH, YouTube and Vimeo selection. Gallery search previews exercise the embedded YouTube provider;
Dubbing uses the custom Vidstack video controls for local and normalized URL imports.
Native video MIME hints select a provider before its `<video>` element connects, and
extensionless Dubbing routes use the backend's normalized MP4 type. Those endpoints
also support cheap metadata-only `HEAD` requests. Workflows can pass any other
supported source through the same shared player without creating another playback
stack.

Video previews use a glass gradient overlay and expose play/pause, 10-second seek,
mute, volume, playback rate and enter/exit fullscreen controls. Dubbing keeps one
Vidstack instance while changing preview sources, adds revisioned byte-range URLs,
and displays the source thumbnail while a first preview is prepared. Audio preview
buttons show pending/buffering and unavailable states instead of silently swallowing
playback failures. Playback labels are translated in all 21 locales.
