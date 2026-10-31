# VoiceStudio Android

Native Kotlin / Jetpack Compose app with phone-first speech, voice cloning, dubbing and dictation,
plus an optional connection to a user-owned VoiceStudio computer. Installed
models run offline. No cloud relay, analytics transmission, crash reporter or
push service is used.

## Prerequisites

- JDK 21 (AGP does not support the machine's default JDK 27).
- Android SDK platform 36 and build tools; minimum device API 29.
- Internet for the first Gradle/dependency/native-source download; NDK
  27.1.12297006 and SDK CMake 3.22.1.
- Optional: a VoiceStudio computer with Network sharing enabled, or a private
  server/Tailscale URL, for computer tasks. Phone model installation and
  licence acceptance are available without a computer.

From this directory:

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
# Elsewhere, use your own JDK and SDK paths.
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > local.properties
./gradlew assembleDebug
./gradlew testDebugUnitTest
./gradlew lint
./gradlew assembleDebugAndroidTest
./gradlew installDebug
adb shell am start -n sh.voicestudio.mobile/com.voicestudio.mobile.MainActivity
```

Windows: Gradle uses `gradlew.bat`; native preparation also needs Bash and
Python 3, with equivalent JAVA_HOME / ANDROID_HOME settings. This host verifies
the macOS build path.
`local.properties`, build outputs and local IDE state are ignored. Gradle 8.13,
AGP 8.12.0, Kotlin 2.2.21 and direct dependencies are pinned in the wrapper and
`gradle/libs.versions.toml`. The wrapper was generated using installed Gradle
9.8.1. Compilation runs inside Gradle to avoid Kotlin daemon files outside
sandbox writable directories; parallel workers are capped for this shared host.
The version name/code are derived at configuration time from root `package.json`.

The adaptive launcher icon (including its themed monochrome layer) and launch
screen are generated during Gradle preBuild from `docs/logo.svg` and the default
canvas token by `scripts/generate_brand_resources.py`. Generated resources stay
in `app/build/`; no copied logo binaries are checked in.

## Layout

- `app`: launcher, Navigation Compose, consent, generated asset packaging, JVM UI
  smoke/contract tests.
- `core/designsystem`: immutable generated `VSTokens`, token-to-Material role
  mapping, shapes, Inter typography, appearance preferences.
- `core/network`: typed REST responses and multipart operations, guarded OkHttp
  transport, private-address policy, cancellable requests and ticketed dictation.
- `core/data`: DataStore preferences/saved hosts, AES-GCM/Android Keystore session
  vault, durable phone/computer WorkManager dub pipelines and local notifications.
- `core/local`: typed catalogue, resumable verified downloads, local licence
  receipts, shared inference/deletion leases, private voices, disk-backed dubbing
  checkpoints, platform codecs and the `LocalEngine` adapters.
- `core/l10n`: nested catalog lookup, interpolation, locale/plural fallback.
- `features`: separate `connect`, `speak`, `voices`, `dub`, `dictate`, `settings`
  packages with shared native controls, playback, capture and permission helpers.
  The six feature packages share one Gradle module to avoid six identical build
  configurations and cross-module UI plumbing.

## Screens and behavior

All five tabs are reachable without pairing. Consent dismissal means No.
Speech, Clone, Dictate and Dub remember their own Run on choice. An installed
phone model is preferred; otherwise the paired computer is used or a
compact card offers Get with the total download size. Dub groups its ASR,
translation and cloning dependencies in one card; each gated model has its own
licence confirmation. The card also opens Models. Run on is a compact This phone / Computer control visible only when paired. Phone and
computer voices remain separate, with a location badge in the combined list.
Settings opens optional Connect and Models. Resuming a download counts only missing
bytes plus a small reserve, skipping verified completed files. Model choices use product names and
localized capabilities; licence details stay inside the acceptance sheet.

Android follows the iOS workspace hierarchy: compact inline headers, a rounded icon
dock with localized accessibility labels, native mobile card radii and a two-column
Home task grid (one column at larger font scales). Home keeps its brand banner short,
puts the privacy explanation behind its info action, and shows contributors in one
scrollable row with a fixed All link; the six task cards fit above the dock at
360 × 800 dp with the default text size.

[Home screenshot](../../docs/media/mobile/android-home.png) · [Speech screenshot](../../docs/media/mobile/android-speak.png) Settings groups related rows;
Empty voice and dictation screens show a centered explanation and their primary action.

Speech has a large editor with placeholder-only copy, grouped voice/model/language
rows, a fixed Generate action above the dock, a compact player and recent rows. Switching speech models resets any
preset speaker outside the new model's range; Generate requires a valid speaker. Dictate places Copy and Share beside the microphone above the dock while the transcript scrolls: tap
starts/stops; hold records until release. Connect keeps URL/PIN
under Enter address and the optional API key under More. Model licences open in
a sheet with complete inert terms, a rights confirmation and Accept / Decline.
Microphone permission explains both local processing and optional computer routing using the same translated copy as iOS.
Screens use short labels and icons; hints live behind one info action. Settings
uses distinct row icons without explanatory paragraphs. Pickers mark the current
selection, use normal text colors, and show palette color swatches. Errors show a short title, the localized cause and recovery step, and a recovery action.
Workers retain structured error keys and parameters across resumes.

Connect supports CameraX/ZXing QR scanning, manual URL/PIN/API-key entry, saved
hosts and editable labels; a successful scan reveals the address, PIN and Connect action.
Last-seen timestamps are retained locally. Public `/health` must carry the backend
marker and expected status/version; `/profiles` verifies consumption access.
Master keys only enter the session exchange and are never persisted. Session
and PIN ciphertext is stored using a Keystore AES-GCM key; DataStore contains
only connection metadata/preferences and dub checkpoints. A 401 returns to
pairing when it belongs to the active connection; a failed attempt on another
host leaves the active session intact. Disconnect revokes an administrator
session when one exists and clears local access even offline. PIN-only pairing
has no session to revoke. Reconnecting retains a saved host's label and updates
its last-seen timestamp. Unreadable preferences or malformed persisted JSON
fall back to defaults rather than preventing launch.

Connecting/reconnecting resets host data, the navigation tree and its saved
forms, pending document targets, players and dictation controllers. Switching to
a different host or disconnecting cancels the old host's WorkManager jobs and
clears dub checkpoints; checkpoint keys also include the host, and late writes
from inactive hosts are rejected. Reconnecting to the same host retains durable
job monitoring. Long jobs already running on the old computer continue there.

All forms resize their scroll viewport for the keyboard. Background taps,
primary/secondary actions and navigation dismiss focus; single-line fields have
the native Done action. The multiline Speech editor keeps Return for newlines
and offers a visible Done toolbar while focused. Rename and picker dialogs use
their own window's focus manager. Android retains its normal keyboard-first Back
handling; reaching Generate only requires scrolling or Done.

Picker labels take their intrinsic single-line width; values fill the remaining
space, align to the trailing edge and ellipsize to one line. The full value
remains in the accessibility description, and choice lists allow two lines per
name. This applies to voice, engine, language, palette and dub targets. Every
searchable list shows the query when no matches remain.

Tabs render their content without navigation fades. Language catalogs and locale
mapping load off the UI thread and are cached; Speech and Dub show inline loading
indicators with their controls already visible. History/profile refreshes show
inline progress rather than hiding the screen. Dictate keeps its controls visible
while checking readiness.

Speech has text, backend voice/engine/language pickers, generation, host-streamed
Media3 playback, an accessible seek/progress slider, system share/save, and
recent history with star/delete. Voices has search, previews, rename/delete,
permission-gated recording or file selection and explicit clone consent.

Computer Dub keeps a device document's `OpenableColumns.DISPLAY_NAME` separately from its
private cached path, shows that name, and sends it as the upload filename so host
history retains it. Remove deselects the private copy and names the file for
accessibility. History uses grouped title/metadata rows with compact, named save
and share icons. Save opens the system document picker; Share opens the share
sheet.

Computer Dub accepts device media or a link for the host to ingest, multiple target
languages and an explicit voice choice: **Default voice (engine preset)** or a
saved profile. Computer Dub has no source-voice choice. Desktop source cloning uses
`dub-session.ts:readTranscript` and `/dub/transcribe-stream/{job_id}` to prepare
`cast_sources` / `speaker_clones`, then `applySpeakerCloneDefaults` assigns
`auto:` profile IDs carried into `/dub/generate/{job_id}` by `segmentGenInputs`.
The synchronous `/dub/transcribe/{job_id}` used by M1 returns no cast-reference
metadata. The computer flow therefore uses the preset/profile choice;
an empty `profile_id` explicitly means the engine preset, never source cloning.

Before creating a computer job, Dub fetches `/engines/translation`, selects exactly its
`active` ID, and requires positive `installed` and `ready` fields. Missing or
unready engines direct the user to VoiceStudio Settings on the computer. An
`online` engine names the service and requires confirmation for that job using
equally styled Allow/Decline buttons; dismissal and decline start no upload or
translation. LLM engines also require that consent unless the resolved
`dub_translation` skill and provider metadata positively identify a loopback
endpoint on the host. `/api/settings/llm-skills` and `/api/settings/llm-providers`
expose this metadata on authorized server hosts; desktop LAN hosts forbid these
admin reads, so an unknown LLM route requires consent. The public translation
inventory's `configured_via` names the service when available. Provider names and
the provider's `local` flag alone do not prove locality: base URLs can be
overridden. Only literal loopback IPs and `localhost` qualify, without any DNS or
provider calls from the phone. Approval is scoped to a single durable job. The
worker rechecks the active provider and a fingerprint of its resolved endpoint
before starting/resuming and immediately before every translation request, and
refuses changed routing; every
`/dub/translate` body contains the explicit authorized `provider`. Existing jobs
without provider metadata fail with recovery advice instead of using a fallback.
WorkManager copies no credentials into job input. It
uploads, waits for prep, transcribes, translates each track, starts generation,
and replays persisted job events while polling. Checkpoints resume existing host
tasks after app process death or connectivity loss. Abort stops the host before
cancelling the worker. Downloads use the guarded client and a FileProvider share.
The worker requests a data-sync foreground service for long uploads. If Android
refuses that start, work continues within WorkManager's normal execution limits;
cancellation still stops it. The merged manifest declares `FOREGROUND_SERVICE`,
`FOREGROUND_SERVICE_DATA_SYNC`, `FOREGROUND_SERVICE_MEDIA_PROCESSING` and a
WorkManager service declaring both types. Notification
permission is not required to upload/promote the service; it gates optional
completion notifications. Security failures and initialization/input failures
produce actionable Dub errors. Failed job receipts are shown once and acknowledged
in DataStore across screen recreation, app restarts and host switches. Cancelled
jobs show a cancellation status instead of a failure. Successful job download
actions remain available. Optional completion notifications are generated locally,
without a push service.

Computer Dictate checks readiness and streams mono 16 kHz signed PCM over the host
WebSocket, using a one-use ticket for sessions. Tap/hold controls, partial/final
transcripts, copy/share and lifecycle-based microphone cleanup are included.
Sherpa utterance finals append without stopping capture. Only summary/legacy EOF
finals or socket termination end a session; authoritative/refined summary text
replaces the live transcript without duplication, and empty summaries preserve
committed phrases. Unknown final kinds are ignored, matching desktop. Callbacks
and cleanup are scoped to the recording generation, so an old stream ending
cannot close the next session.
Readiness details, including the missing model and desktop install location,
are behind its info action. A share PIN alone does not authorize dictation. Settings includes live appearance, per-appearance palettes,
system/desktop languages, notification permission, privacy, version and licenses.

## Shared inputs

Run these from the repository root only when intentionally regenerating shared
inputs (this Android implementation does not edit them):

```sh
uv run python scripts/export_openapi.py
bun scripts/export-mobile-tokens.ts
```

Gradle's `:app:bundleCatalogs` copies all Electron locale JSON files and
`mobile/contract/strings*.en.json` into generated assets on every input change.
It also bundles `electron/src/shared/languages.json` (the canonical display
names, with Auto first) and generates the manifest label from English `app.name`.
Only the selected locale's fallback chain is parsed, with catalogs and L10n
instances cached per locale. Chinese script/region tags map to the bundled
zh-CN (Hans) and zh-TW (Hant, HK, MO) catalogs before English fallback.
No copied catalogs are checked in. New Android-only keys live in
`mobile/contract/strings.android-additions.en.json`; untranslated mobile keys
fall back to English until the maintainer integrates their translations.
The checked-in generated token file is never edited by Android development.
OpenAPI is read as the contract; typed models reflect loose responses verified
against backend router implementations. No guessed endpoints are used.

Inter 4.1 variable TTF comes from the official rsms/inter GitHub release.
The release zip SHA-256 is
`9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`.
`OFL.txt`, `Apache-2.0.txt` and dependency notices are bundled under licenses and
available offline in About.

## Contract decisions and limits

- Android network-security XML cannot express IP CIDRs. The XML enables the
  transport; the sole OkHttp client is the security boundary: HTTP URLs must be
  loopback, RFC1918, CGNAT, IPv4/IPv6 link-local, IPv6 ULA, `.localhost`, `.local`
  or `.ts.net`. DNS results for HTTP must also be private, preventing rebinding
  to a public address. Other hosts require HTTPS with system trust. Redirects,
  unpaired origins, URL credentials and credential query parameters are rejected.
  Media3 uses this same client; no WebView/DownloadManager bypass exists.
- There is no `/languages` route. Both pickers use the build-bundled desktop
  `languages.json`; Speech defaults to Auto, or a selected profile's language.
  Dub excludes Auto, maps names to ISO codes through Android locale data when
  available, and preserves the canonical name for long-tail languages. The
  host determines which languages its selected translator/engine supports.
- `/engines` is loosely typed in OpenAPI; its router supplies `display_name`,
  `active`, `available`, `reason`, `hint` and `supports_cloning`. Select the active
  engine when eligible, otherwise an eligible fallback. A reference-bearing
  profile requires positive cloning support. Default voice sends no `profile_id`.
- Palette labels come directly from generated tokens, except Default uses the
  desktop `themeAppearance.default` translation. The large native screen title
  derives its scale from editor/UI tokens; controls/cards use 8/10 dp token radii.
- `/generate` returns a WAV with `X-Audio-*` headers, not JSON. Playback streams
  its canonical `/audio/...` host file. The progress scrubber satisfies the
  waveform/progress option; no synthetic waveform is fabricated.
- Computer Dub is the host's ingest/prep → transcribe → translate → generate pipeline,
  with `/jobs/{id}/events` replay and `/jobs/{id}` status. WorkManager retries
  transient transport/service failures and keeps completed stage checkpoints.
  The backend has no atomic idempotency key for starting generation; a process
  dying after host acceptance but before checkpoint persistence can repeat that
  stage. Remote jobs themselves continue on the host.
- Models and their licence acceptance are managed on the phone for phone
  tasks. The separate computer model-licences link opens the paired host UI.
  Computer model setup remains on the host; no admin endpoint is exposed.
- No blur or custom motion is used. Compose follows the system animation scale;
  long content scrolls, text scales, touch targets are at least 48 dp. Large font
  scales show labelled navigation icons without squeezed tab text. A visible
  text field or backend-provided name is used for each interactive choice.

## On-device models and inference

Gradle bundles the generated `mobile/contract/models.json` with the locale
catalogues. Unknown fields are ignored; invalid revisions, checksums, file
paths, sizes, product names, runtime/capability pairs and schema versions fail
closed. No catalogue or model is fetched when a tab is opened. Screens use
product names and localized capability labels from that bundle.

Models cards show name, size, capability and one Download / Cancel / Delete
action. Their licence action opens languages, declared terms and actual storage;
Delete there can also remove partial downloads. The list shows total model storage. Models above the phone's
physical RAM are disabled, using decimal GB (`min_ram_gb * 1,000,000,000`).
Downloads start only from a user action, use Hugging Face with hf-mirror.com
fallback, resume with HTTP Range, enforce Wi-Fi unless mobile data is explicitly
allowed, and reject low storage. A server ignoring Range restarts that file.
Redirects stay HTTPS on the default port, on Hugging Face/HF CDN or mirror hosts
and their subdomains, matching iOS. The dedicated
client never carries computer credentials. Incomplete files remain resumable;
Delete removes them too. Every file must match size and SHA-256 before an
installation marker is written; native loads reuse verification while the file stamp
(size, modification time and catalogue identity) is unchanged.

Phone files and local licence receipts live under Android's `noBackupFilesDir`;
they are app-private and excluded from cloud and device backups. Non-commercial,
conditional, unknown and explicitly gated licences block download and load until
accepted locally. The dialog leads with a plain summary (category, rights rows, notes, https licence links) and keeps the bundled complete inert `licence.terms`
disclosure, attribution and fingerprint under a collapsed Exact terms section. Receipts store that fingerprint, app
version and timestamp; the latest 20 accept/withdraw actions are retained.
A changed fingerprint or withdrawal blocks use without deleting weights.

`LocalEngine` exposes cancellable, progress-reporting TTS, reference cloning,
offline transcription, streaming transcription and translation. Feature screens
call only this interface. Timed ASR exposes speech windows with native Whisper
token timestamps, retaining window bounds when the runtime provides no timing.
sherpa uses Kitten/Kokoro/Piper/Supertonic for TTS and Pocket/ZipVoice for reference cloning, Whisper in at most
20-second windows for offline ASR, and Zipformer with utterance endpoint resets
for live dictation. Tap/hold capture releases on lifecycle stop and screen
exit; normal Stop drains the recognizer for its final transcript. Generated
speech supports local playback, scrubbing, sharing and Save. WAV exports pass
`markSynthetic`, which writes a RIFF INFO synthetic-audio provenance marker.

Voices lists Phone and Computer badges. Phone cloning records or picks a local
clip (up to 30 seconds), decodes it to 24 kHz mono with MediaExtractor/MediaCodec,
uses the selected Whisper model for the reference transcript, and publishes its
private reference audio, name and consent time atomically. Cancelled or failed
creation removes the unpublished voice. Rename and Delete operate on its phone
storage. The clone task remembers its own Run on choice; creating a phone voice
requires ASR, while speaking with it requires an installed, accepted OmniVoice.
Speech lists presets and phone voices in one picker, switches to the cloning
model for a phone voice, and stores generated WAVs and compact recent rows
privately. Exports are copied into the share provider's cache on an IO thread.
Native inference is serialized across phone tasks to bound memory use. Kokoro
receives the selected language; Kitten offers English. Cloning uses the runtime's
automatic language detection and shows Auto in the language chip.

Phone Dub accepts a local media file, target language chips and either Original
speaker or a saved phone voice, with explicit recording/voice permission. More
contains model choices and Audio only. It never fetches pasted Internet URLs;
links remain in the computer flow because phone networking is restricted to
explicit model downloads and the paired computer.

The phone pipeline decodes with MediaExtractor/MediaCodec into disk-backed
24 kHz mono PCM. It preserves timestamp gaps and pads to the container duration.
Whisper transcribes in at most 20-second windows and supplies token timing for
the speech bounds. Each window is a translation/synthesis segment; the catalogue
prompt supplies automatic source-language detection. llama uses its bounded
2048-token context and 512 generated-token limit with greedy sampling. Original
speaker mode cuts up to eight seconds from that segment and transcribes that
exact reference before OmniVoice cloning. A saved voice is snapshotted into the
job instead. OmniVoice resolves the catalogue's tokenizer GGUF as its codec.

Short synthesized segments are padded; longer ones use waveform overlap-add
compression to fit their speech bounds. Segments are mixed on a silent timeline,
replacing the original soundtrack. Music/stem separation and multi-speaker
separation within one Whisper window are not performed. The original video
track and orientation are copied with MediaMuxer; generated PCM is AAC-encoded
for MP4. Audio-only inputs or Audio only export WAV. WAVs pass `markSynthetic`;
MP4 also carries an `application/voicestudio.synthetic-audio` metadata track.
Unsupported platform codecs produce an error; Audio only is available when
creating a job. Inputs are bounded to four hours and twenty target languages;
storage and declared model RAM are checked before processing.

Jobs run as WorkManager foreground work with stage/percent/Cancel and no
connectivity constraint. Android 15+ uses mediaProcessing; older devices use
dataSync. Notification permission gates optional completion notices, and does
not gate foreground processing. Android foreground execution limits still
apply. Atomic checkpoints retain extracted PCM, timestamped segments,
translations, exact reference text and hashes of fitted segment files. Process
restart resumes existing work; cancelled/failed jobs expose Resume and Delete.
Changed model/catalogue identities fail closed. Native loads always recheck licences. Model SHA-256 verification is cached per file
using size, modification time and catalogue identity; changed stamps and native failures
invalidate it. The cache is shared by UI and workers for the process lifetime; a fresh
process verifies files on first load. Native inference owns its lease until native
cleanup returns, including after cancellation. UI and workers share model leases and
licence state.
Whole-segment reference clips reuse their segment transcript; shortened clips fall
back to it if ASR returns blank. The chosen transcript is checkpointed before cloning.
A mix is rebuilt from completed segment files on resume, preventing duplicate audio. Completed jobs release their source and intermediate PCM, retaining
private output/history with play (including video), share, save and delete.

Recording auto-stops at 29 seconds to leave encoding headroom under the 30-second
phone reference limit. Unreadable voice or dub records are skipped individually;
installed-model state remains available.

Native builds, checksums and cancellation boundaries are documented in
[`../native/android/README.md`](../native/android/README.md).

## Verification

Run from this directory with JDK 21 and the SDK environment above:

```sh
mkdir -p build/hf-empty
export HF_HUB_OFFLINE=1
export HF_HUB_CACHE="$PWD/build/hf-empty"
./gradlew assembleDebug
./gradlew testDebugUnitTest
./gradlew lint
./gradlew assembleDebugAndroidTest
```

Debug app and instrumentation APKs target arm64-v8a and x86_64. The JVM checks
run without a device; instrumentation needs maintainer device/emulator execution.

JVM checks cover existing M1 API/security, storage, worker, picker, protocol and
Compose smoke regressions, plus generated logo/adaptive/splash resources, compact download prompts,
separate queued licence sheets, picker marks/colors/swatches, Dictate control
spacing, local catalogue/routing, licence persistence and
withdrawal, HTTP resume and restart, checksum rejection, mirror fallback,
Wi-Fi/mobile-data policy, remaining-byte resume storage, verification-stamp caching,
cancellation leases, deletion, corrupt-record isolation, QR Connect visibility,
model-switch speaker resets, actionable error details, synthetic WAV
metadata, native cancellation control and microphone lifecycle cleanup. Phone
voice checks cover consent, reference/transcript persistence, cancellation,
rename/delete, picker routing and streaming resampling. Phone dub checks cover
multi-language output, timing/padding/mixing, provenance, checkpoint replay,
corrupt render recovery, blank-reference transcript fallback, persisted error parameters,
model/licence changes and foreground cancellation. The
launch → consent → Settings → Connect → mock host → Speech → Play smoke preserves
the computer flow. Tests use local mock servers and an empty HF cache.
Robolectric's API 34 instrumented SDK is resolved by Gradle into app build
outputs and then used offline.

`LocalInferenceTest.allPinnedNativeLibrariesLoadTogether` checks native symbol
resolution on ART. The real model smoke is enabled only with instrumentation
argument `localInferenceSmoke=true`. It uses a separate private cache and requires
explicit prior review and `acceptModelLicencesForSmoke=true` for gated models.
`downloadSmokeModels=true` explicitly provisions the three smallest TTS/offline
ASR/streaming ASR selections; omit it on subsequent offline runs. It asserts
nonempty audible PCM, Whisper recognition of generated speech and a final
Zipformer transcript. The `android-inference` job in `.github/workflows/mobile.yml`
runs this smoke on an x86_64 emulator with explicit test-model provisioning;
physical-device validation remains separate.

`PlayScreenshotTour` captures the Google Play phone screenshots from the real
app: it runs only with `playScreenshots=true`, provisions the smallest TTS model
into the app's own model store with the same explicit `downloadSmokeModels` and
`acceptModelLicencesForSmoke` flags, saves one phone voice ("Narrator", a
reference clip spoken by that model) through `PhoneVoiceStore`, declines
analytics, generates one take on the phone (and fails if its player never gets a
duration), picks two dub target languages and saves `01-home.png` … `07-settings.png` to the app's external files
directory (`play-screenshots/`). It checks the fixed Generate action directly and scrolls the form content rather
than the fixed workspace headers. It asserts Play's size rules, so use a 16:9
device; the `android-screenshots` job uses the Nexus 5X profile and uploads the
`android-play-screenshots` artifact.

`cloneInferenceSmoke=true` adds real WAV decoding, Whisper reference creation
and OmniVoice cloning, including persisted rename/delete. `dubInferenceSmoke=true`
adds an audio dub through real Whisper, llama and OmniVoice. Both use explicit
licence/download flags as above; they are off by default. All model smokes check
the device's declared model RAM before loading. The unconditional
`PhoneDubInferenceTest.platformCodecsMuxVideoAndSyntheticAudioWithOrientation`
generates its own AVC fixture, encodes/muxes synthetic audio, and checks tracks,
orientation, duration and decoding. No fixture/model binaries are committed.

Local validation on 2026-10-18 builds the app and instrumentation APKs, passes all 136 JVM
tests and lint, and runs the Android 14 emulator checks: app launch, localization
and error handling on ART, loading all three native runtimes together, and AVC/AAC
muxing with orientation and synthetic-audio metadata. An explicit full-model run
also passes all eight instrumentation tests with zero skips: Kitten speech,
Whisper/Zipformer recognition, persisted reference voices and OmniVoice cloning,
and a complete Whisper → French translation → OmniVoice audio dub. The run takes
about 20 minutes on the local ARM64 emulator, including model provisioning.
Inference quality, audible playback, microphone permissions,
large-font/TalkBack rendering, physical-device RAM and background/notification
behavior require their separate model/device checks.

## Electron-style mobile Home

Home is the first workspace after consent, using Electron's bundled signal-field
artwork, shared palette roles, Inter and card hierarchy. Speech, cloning, dubbing,
dictation, models and computer pairing have working shortcut cards. Generated
audio, saved voices and recent dubbing projects come from the actual phone
libraries, with paired-computer entries alongside them. Audio opens its player,
saved phone voices select the reference in Speech, and projects open their
outputs and resume controls. Empty states are localized and downloads remain
explicit. Home replaces Speech in the five-item navigation; Speech opens from Home.
Desktop-only workspaces are not advertised as available mobile features.

## Home, Dub and dock

Home marks debpalash and velixio with a small core-maintainer star; TalkBack announces the role.
Home shows Electron's contributor avatars (`HomeContributors.kt`; the Gradle catalog task copies the
images from `electron/.../home/contributors` into the APK assets, nothing is fetched at runtime and
`tests/test_mobile_contract.py` guards the list; `scripts/update_contributors.py` regenerates it). Dub opens with a dashed source card and the animated
`DubGlyph`. The compact dock uses an accessible icon for each workspace, with 48 dp touch targets and native Tab selection semantics.


Speech lists all seven phone speech engines independently of the selected voice.
OmniVoice, Pocket TTS and ZipVoice require a saved voice and show guidance until one is
selected. Choosing a preset-only engine clears an incompatible reference;
Voice Design remains a separate explicit task. It
shows only OmniVoice. The shared catalogue pins all component files, including
ZipVoice's separate vocoder and bundled lexicon; every file is size/hash checked.
Downloads and licence review remain explicit. See the shared contract for the
conversion-specific licence restrictions.

OmniVoice Speech requires a saved voice and rejects bare synthesis before model
loading; it cannot silently route to voice design. The shared pack uses the Q8_0
base and native F32 codec (1.4 GB, 8 GB RAM), retaining the stable legacy model
identifier and existing licence acceptance. Models downloads the replacement
files explicitly; previous recordings and files are preserved. Native speech
validation checks recognizable words and rejects repetitions.
