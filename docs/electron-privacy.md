# Electron privacy and retention

> **Historical context:** this page was written while the Electron and Tauri apps
> coexisted. Mentions of Tauri helpers, pages, tests and regression results describe
> that migration period; the Tauri shell has since been removed and the shared code
> now lives in `electron/src/shared/`. Existing Tauri installs: see the
> [migration guide](electron-migration.md).

Settings > Privacy exposes the existing invisible-watermark setting, analytics consent, automatic error reports and generation-history retention.

Diagnostic bundles and prefilled bug reports scrub known credential query
parameters, including share PINs, API keys and passwords. The backend and
Electron report builder also recognize percent-encoded parameter names, preserve
unrelated query parameters, and do not treat a substring such as `spin` as `pin`.
Quoted credential values, including log-escaped quotes, are removed in full. Query scanning uses a finite
known-name bound so malformed log lines cannot trigger repeated suffix scans.
Enclosing URL bounds are verified from structured JSON or the complete URI
token; ambiguous quoted tails are redacted conservatively.

Manual bug reports prioritize the existing scrubbed, bounded faulting-thread
excerpt for the current native backend exit ahead of verbose status tails. This
keeps useful crash context within the issue URL's size budget; it does not fix
or infer the native crash's cause. Ordinary reports retain their section order.
The report still opens for review before submission, with no automatic raw-log
upload or additional network request.

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
failures. An internal generation deadline remains a reportable timeout, distinct
from a user pressing Cancel. A no-progress observation does not cancel a valid running operation.
Reporting is best effort: an abruptly terminated or offline process cannot
guarantee delivery; eligible crash summaries can be recovered on a later launch.

Packaged builds configured with an app ID and write-only ingest key send failure
reports to Supermetry's app intake (`/api/public/v1/app-events`). The key identifies
the application, never the user, and cannot read reports. It ships in the app's
private build metadata; it is never committed, logged by the app, or included in
stored reports. Release builds receive the app ID and ingest key only through
GitHub Actions Secrets, never public repository variables; build commands must
not print either value. The default host remains `https://app.supermetry.com` from
`config/analytics.json`. A separately configured `SUPERMETRY_APP_HOST` selects
an operator's instance. The owner-configured packaged destination is
`https://app.voicestudio.sh/api/public/v1/app-events`, owner-set
2026-11-16; any other production host needs owner approval.
Release workflows read the repository secrets `SUPERMETRY_APP_ID` and
`SUPERMETRY_INGEST_KEY`. The destination's existing `SUPERMETRY_APP_HOST`
variable/secret override is separate and unchanged. Fork builds receive no
upstream app credentials; unconfigured builds remain credential-free.
The app intake stores installation and optional session IDs only as keyed hashes.
Without valid app credentials, the existing public events request
(`/api/public/v1/events`, event `diagnostic_report`) and site remain unchanged.
Usage analytics keeps its existing destination and request format.
`config/diagnostics.json` is the shared closed metadata policy.
Desktop process crashes carry a closed code that names the path, never a message:
main-process uncaught exception or unhandled rejection, GPU process gone, utility
process gone, or another Electron child process gone (the child's free-text service
name is not read). A generation deadline reports `GPU_JOB_TIMEOUT`.
Reports contain technical categories/codes, app/build/platform labels,
known first-party source locations, bounded counters and a separately generated
random installation ID. They never contain audio, text/transcripts, user names,
filenames or home paths, credentials, URLs, logs, exception messages, source
lines, local variables, memory dumps, screenshots or manual report bodies.
Fingerprints use sanitized technical fields only. No cookies, user credentials or
referrer accompany requests; only app intake uses its write-only app key.
Redirects are refused. Diagnostic
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
stable report IDs. One fingerprint may hold at most eight of those slots, so a
failure that repeats often enough drops its own oldest record rather than
evicting unrelated evidence that is still inside its retention window. Desktop
allocates that total between main and the Python
inbox. Recovered crashes retain the original accepted fault or heartbeat timestamp;
restarting does not extend their retention. Turning reports off stops delivery
immediately, cancels active transport, invalidates the consent epoch and purges
queued reports. A new opt-in creates a
new identity; old queues and pre-consent crash history do not become eligible.
Locked retired files are retried every minute. Collection under a new opt-in
waits for that cleanup, so a delayed purge cannot delete new reports.
Completed-delivery bookkeeping stays local and prevents repeated uploads while
producer files are locked. If completion cannot be saved before an abrupt exit,
a restart may retry the same stable report ID; exactly-once delivery is not promised.
Within a run, rereading the same grant after a storage error preserves unsaved
reports and completed-delivery bookkeeping.
App-intake batches contain at most 20 events and 16 KiB of encoded JSON. Acknowledged
reports and permanent `too_old`, `invalid`, or `duplicate` drops are retired;
server throttling and transient failures retain stable IDs for bounded retries.
An unauthorized app key pauses sending for the rest of that app run and retains
the queue. Delivery results contain only fixed local status codes and counts.
Temporarily unread consent stops collection and delivery while saved evidence waits
for a bounded retry. Phone Settings and iOS crash/upload handlers follow the
recovered current grant; an early Retry never presents unread consent as a saved
OFF choice. Automatic read retries wait one minute, while a user-clicked Retry
may reread sooner. Saving a choice never replaces unread shared preferences;
a hard-disabled launch still invalidates an unread old grant before it can resume.
Reopening a saved failed job only displays its old error; it does not create a
new report under a later consent choice. Resumed operations report only failures
proved fresh under the same grant. The existing job-events API supplies an
indexed `event_watermark` (true retained-tail sequence and server time) from the
same read snapshot as its bounded ascending page. Verification starts after that
watermark; it never scans all history. Older servers without the watermark still
show job progress, but cannot authorize resumed-history diagnostics. Fresh tasks
submitted under the current grant retain their independent submission proof.
Changing consent in another window invalidates
delayed captures. Uncaught startup errors whose reporting permission was unknown
remain local; a later opt-in cannot authorize them retrospectively.
Requests, optional installs, native screen actions, uploads and player/picker
callbacks retain the permission observed before their work starts. Restored
callbacks without that observation stay local. Python exceptions retain their
original permission through interpreter/thread hooks after cleanup; delayed
desktop setup/start attempts and child-exit recovery follow the same rule.
Restarts keep their original permission even while waiting for the previous
backend to stop; observing failures does not change promise results or whether
an error is handled.
Runtime/file IPC work observes permission at entry. Its asynchronous descendants
retain that observation through Node's fatal-error and unhandled-rejection hooks.
Uncaught faults with unknown provenance remain in the local journal rather than
borrowing permission at delivery time or after restart. A consent-stamped process
marker can still recover a coarse unclean exit, but raw journal timestamps do not
authorize fault details or frames. Confirmation actions retain the choice
observed at the click; an unrelated or rewrapped promise cannot acquire a newer
grant. Seeking an existing phone playback does not renew its grant.
Permission dialogs, voice-edit waits, gallery loading and stalled transfers do
not renew an operation's permission. The initial phone action owns its entire
permission rationale and OS prompt; confirmation cannot acquire a later grant,
and cancelling or dismissing the rationale discards that action's observation.
A cached phone download failure from an earlier transfer remains local, including
after Pause and Resume; a genuinely new transfer retains the permission observed
when that transfer starts.
Temporary queue-read failures preserve current in-memory reports and completed
delivery decisions. Crash recovery retains its report until the prior session
marker can be replaced; orderly close removes only a marker owned by that process.
A failed consent write is shown with Retry and leaves the current process off.
On iOS, unread diagnostics consent also keeps reporting off and offers Retry.
After the initial privacy check finishes, that read failure does not block
opening shared files, shared text, or workspace shortcuts.
Where storage permits, withdrawal replaces the previous grant or writes a
private revocation marker so restart also remains off. If every write or
restoration fails, durable withdrawal cannot be guaranteed; the save fails
instead of acknowledging a persisted choice.
After a choice is saved successfully, a fresh status read can observe another
client's new opt-in. Failed or pending writes remain locally closed; an older
operation cannot acquire that fresh grant.

In source/Docker builds, switching off closes cached permission and cancels the
sender before waiting on storage. The Settings request completes only after
saving that choice, or returns a retryable save error. Slow storage can keep the
save pending without allowing collection or delivery to resume. Permission-denied
storage errors are not confused with desktop-managed consent.
Desktop crash-session recovery uses private local IDs. Separate app-intake session
pings are implemented behind `diagnostics.sessionPings`, **false by default** in
generated build metadata. They additionally require diagnostics consent and are
never enabled by usage-analytics consent. An explicit build setting
`SUPERMETRY_DIAGNOSTICS_SESSION_PINGS=1` enables this owner-decision flag. When
enabled, a run sends one random session ID in a session event and its new failure
reports; queued reports from earlier runs never acquire the new run's ID. Turning
consent off retires that correlation for the rest of the run. With the flag off,
there are no session pings or session IDs in failure requests; Supermetry can
calculate crash-free users, without crash-free sessions. The consent copy is unchanged.

`OMNIVOICE_DIAGNOSTICS_DISABLED` or `OMNIVOICE_ANALYTICS_DISABLED` set to
`1`, `true`, `yes` or `on` disables automatic reports.
Loading either hard-disable switch also revokes a previous diagnostics grant
and purges its queue; removing the switch requires a fresh Settings opt-in.
The usage-analytics consent choice is not changed.
`SUPERMETRY_APP_ID`, `SUPERMETRY_INGEST_KEY`, and `SUPERMETRY_APP_HOST` override
the diagnostics-only generated configuration at runtime, including explicitly
empty or invalid values. Invalid app credentials select the unchanged fallback.
Builds generate git-ignored `config/supermetry-app.generated.json` and bundle it
with the backend; Electron main reads that packaged metadata, falling back to
embedded build metadata when the file is absent, and native builds
generate an equivalent private resource. These values are never exposed through
renderer configuration or the diagnostics status API. Fork and unconfigured local
builds need no app credentials. Product analytics does not read them.
`SUPERMETRY_SITE_ID`, `SUPERMETRY_HOST` and, for the desktop/native fallback sender,
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
