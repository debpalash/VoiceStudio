# Electron privacy and retention

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Settings > Privacy exposes the existing invisible-watermark setting, analytics consent, automatic error reports and generation-history retention.

Watermark controls appear when the backend reports AudioSeal available and affect new audio through the existing marking path. No audio producer bypasses `mark_synthetic`. Analytics consent uses the existing backend opt-in endpoint and remains unchanged until the user explicitly switches it. After consent, the renderer records fixed screen labels and workflow action labels (clone, design, dubbing, transcription); the backend records lifecycle/error events. Privacy-safe pageviews use fixed app paths under `app.voicestudio.sh`, giving aggregate screen counts without sending renderer URLs or dynamic route segments. The renderer never contacts the analytics service itself: it hands allowlisted events to the local backend, which forwards them only while consent is on. Uncaught renderer errors are reduced to a coarse class and stage; no message, stack, credential, home path, text, audio, filename, or user-named content is sent. Raw exception autocapture, DOM autocapture, automatic pageviews, and session recordings remain disabled. Unavailable features do not display inert toggles, and failed saves preserve confirmed state.

The analytics destination is the public Supermetry site id in `config/analytics.json` (host `https://app.supermetry.com`); every build uses it, and `SUPERMETRY_SITE_ID` / `SUPERMETRY_HOST` override it for developers. Nothing is baked in at build time.

Optional VoiceStudio Cloud sign-in validates the discovery issuer and binds its
token and revocation endpoints to that issuer's origin and HTTP(S) protocol.
Missing endpoints, different hosts or ports, and embedded-origin schemes such as
`blob:` are refused before credentials are sent. HTTPS is required outside the
explicit local development configuration; redirects remain disabled. Account
features stay off until enabled, independently of analytics consent.

## Automatic error reports

Automatic reports have their own explicit consent, separate from usage analytics
and off by default. Existing analytics consent does not enable them. A fresh
desktop install asks before runtime setup, so an opted-in installation can
report a setup failure even when Python has not installed or cannot start.
Settings > Privacy changes this choice later. Declining leaves the app usable.

Reports cover observed app/backend/renderer crashes, failed or stalled setup and
downloads, incompatibilities, model loading, engine execution, speech generation
and other failed operations. Normal cancellation, validation prompts, waiting
for a network or scheduled retry, and an intentionally paused download are not
failures. A no-progress observation does not cancel a valid running operation.
Reporting is best effort: an abruptly terminated or offline process cannot
guarantee delivery; eligible crash summaries can be recovered on a later launch.

The destination is the configured Supermetry public events endpoint
(`/api/public/v1/events`, event `diagnostic_report`), using the public site in
`config/analytics.json`. `config/diagnostics.json` is the shared closed metadata
policy. Reports contain technical categories/codes, app/build/platform labels,
known first-party source locations, bounded counters and a separately generated
random installation ID. They never contain audio, text/transcripts, user names,
filenames or home paths, credentials, URLs, logs, exception messages, source
lines, local variables, memory dumps, screenshots or manual report bodies.
Fingerprints use sanitized technical fields only. No cookies, credentials or
referrer accompany the public request, and redirects are refused. Diagnostic
destinations require HTTPS; HTTP is allowed only for literal localhost,
127.0.0.1 or ::1 test collectors.

Electron main owns consent and transmission independently of Python. Its private
diagnostics directory contains consent and sanitized queued records. Managed
Python only writes an epoch-stamped local inbox; it makes no diagnostic uploads.
Source and Docker backends own their own consent and sender through the
administrator-gated diagnostics settings API. A browser connected to a managed
desktop changes consent in that desktop. Uncaught/fatal handlers never wait for
network delivery or flush a crash dump.

The durable queue is limited to 64 records and 512 KiB per installation, expires
after seven days, coalesces repeated failures and uses bounded retries with
stable report IDs. Desktop allocates that total between main and the Python
inbox. Turning reports off stops delivery immediately, cancels active transport,
invalidates the consent epoch and purges queued reports. A new opt-in creates a
new identity; old queues and pre-consent crash history do not become eligible.
Reopening a saved failed job only displays its old error; it does not create a
new report under a later consent choice. Resumed operations report only failures
proved fresh under the same grant. Changing consent in another window invalidates
delayed captures. Uncaught startup errors whose reporting permission was unknown
remain local; a later opt-in cannot authorize them retrospectively.
A failed consent write is shown with Retry and leaves the current process off;
revocation also removes the previous grant or writes a private revocation marker
where storage permits, so a failed replacement does not restore an old opt-in
after restart. Persistent storage failures remain visible until retried.

`OMNIVOICE_DIAGNOSTICS_DISABLED` or `OMNIVOICE_ANALYTICS_DISABLED` set to
`1`, `true`, `yes` or `on` disables automatic reports.
Loading either hard-disable switch also revokes a previous diagnostics grant
and purges its queue; removing the switch requires a fresh Settings opt-in.
The usage-analytics consent choice is not changed.
`SUPERMETRY_SITE_ID`, `SUPERMETRY_HOST` and, for the desktop/native sender,
`SUPERMETRY_APP_URL` support
developer destinations; an empty site disables reporting. Tests use isolated
storage and local/fake transports, never the production site.

Both native phone apps expose the same separate consent and Privacy control.
Their application-owned reporter covers phone and paired-host operation
failures; OS-provided crash/hang information is reduced to the same metadata
policy and recovered only for its original enabled consent epoch. iOS MetricKit
windows must begin after opt-in; Android historical exits must carry the app's
own matching consent summary. OS scheduling and availability limit when these
summaries arrive. Result **Report** sheets are a separate, explicitly submitted
content-report feature and are never included in automatic diagnostics.

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

Backend lifecycle analytics runs in a background worker, so analytics file I/O
does not delay listening or backend readiness. Crash detection still records its
run sentinel before initialization, and version bookkeeping and uninstall-info
cleanup still run with consent off. A later consent choice invalidates queued
startup events and metadata writes prepared under the previous choice.

If the AudioSeal generator fails to load, generation continues without an
invisible watermark. Further load attempts wait ten minutes, avoiding repeated
network timeouts on each render; a later render retries automatically. This
cooldown does not change startup's default cache-only policy; explicitly setting
`OMNIVOICE_PRELOAD_WATERMARK=1` allows startup to download an uncached checkpoint.
