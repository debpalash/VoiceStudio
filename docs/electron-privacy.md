# Electron privacy and retention

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Settings > Privacy exposes the existing invisible-watermark setting, analytics consent and generation-history retention.

Watermark controls appear when the backend reports AudioSeal available and affect new audio through the existing marking path. No audio producer bypasses `mark_synthetic`. Analytics consent uses the existing backend opt-in endpoint and remains unchanged until the user explicitly switches it. After consent, the renderer records fixed screen labels and workflow action labels (clone, design, dubbing, transcription); the backend records lifecycle/error events. Privacy-safe manual pageviews use fixed app paths under `app.voicestudio.sh`, enabling aggregate Web Analytics without sending renderer URLs or dynamic route segments. Uncaught renderer errors also reach PostHog Error Tracking with their message replaced and credentials, home paths, extension frames, text, audio, filenames, and user-named content removed. Raw exception autocapture, DOM autocapture, automatic pageviews, and session recordings remain disabled. Unavailable features do not display inert toggles, and failed saves preserve confirmed state.

Electron release builds pass the publishable `POSTHOG_PROJECT_TOKEN` secret as `VITE_POSTHOG_KEY` and the `POSTHOG_HOST` repository variable as `VITE_POSTHOG_HOST`. The same US destination is baked into the renderer and managed backend on macOS, Windows, and Linux. Keep the token paired with `https://us.i.posthog.com`; a project URL or numeric project ID alone cannot ingest events. Local Electron builds can override the defaults with `VITE_POSTHOG_KEY` and `VITE_POSTHOG_HOST`.

Translation privacy classification is shared with Tauri. Unknown or unavailable backend data does not claim offline operation. The existing `libretranslate` identifier is an alias for the backend's local Argos branch, not an assumption about an external LibreTranslate service. The Translation link opens engine settings.

Retention supports 0 (unlimited), validates whole-number limits up to 100000 and explains that cleanup removes old unstarred takes and their audio after generation. A more restrictive cap requires inline confirmation. Raising the cap or disabling cleanup saves directly. No records or files are deleted by simply opening settings or editing the input.

`electron/tests/privacy-settings-smoke.mjs` covers consent, watermark changes, failed writes, availability, retention confirmation/cancel, unlimited retention and reload with mocked endpoints. Tauri privacy regressions pass after sharing classification. Live read-only checks verified the watermark, analytics and retention schemas without changing the user's privacy preferences or deleting data.

AudioSeal receives 16 kHz audio for embedding and detection, including with
AudioSeal 0.2, which no longer resamples internally. Processing remains bounded
by chunk size. Only the watermark residual is resampled back and added to the
original signal, retaining its sample rate, length, and high-frequency content.

Pending analytics initialization is invalidated when consent is withdrawn,
including a consent lookup or SDK import already in flight. Renderer reports
retain only validated stack frames; multiline error messages are discarded.
