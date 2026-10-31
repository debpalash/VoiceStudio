# VoiceStudio mobile — app contract

Both native apps (iOS: Swift/SwiftUI, `mobile/ios/`; Android: Kotlin/Compose,
`mobile/android/`) implement **exactly this spec**. A feature ships on both or
on neither (CLAUDE.md → "Default features must work on every platform").

The apps run models **on the phone** and can also drive the user's own
VoiceStudio computer (desktop app, Docker or server) over LAN, Tailscale or a
remote URL. **Phone first:** once its models are downloaded every feature works
offline with no computer; pairing adds the computer's engines. No cloud relay,
no accounts. M1 (below) is the computer connection; M2 is on-device inference.

## Shared inputs (generated — never hand-edit)

| File | Source | Regenerate |
|---|---|---|
| `contract/openapi.json` | backend FastAPI app | `uv run python scripts/export_openapi.py` |
| `contract/maintainer-cards.json`, iOS contributor assets | Electron Home maintainer cards and portraits | `bun scripts/export-mobile-contributors.ts` |
| `contract/tokens.json`, `Tokens.generated.swift`, `Tokens.generated.kt` | Electron theme | `bun scripts/export-mobile-tokens.ts` |
| Locale catalogs | `electron/src/renderer/src/i18n/locales/*.json` | copied into each app at build time |

## Theme

- Colors come only from `VSTokens.palettes` (roles = `VSTokens.roles`, same
  names as desktop `THEME_COLOR_ROLES`). No literal colors in UI code.
- Palettes: `default`, `signal`, `canopy`, `current`, `hearth`, `orchid`,
  `heritage` — same ids and labels as desktop `AVAILABLE_PALETTES`.
- Preferences mirror desktop `theme-preferences.ts`: `mode` (`light` | `dark` |
  `system`, default `dark`), a palette per appearance (default `default`). A
  palette lacking the requested appearance falls back to `default`.
- Role usage (match desktop): screen = `canvas`; cards/rows = `surface`;
  sheets/menus = `surfaceOverlay`; primary buttons = `messageAction` /
  `messageActionForeground`, pressed = `messageActionHover`; text = `text`,
  secondary = `textMuted`/`mutedForeground`; separators = `border`; fields =
  `input`; focus ring = `focus`; errors = `error`/`errorForeground`/`errorSurface`;
  warnings likewise; tab/nav bars = `chrome` + `toolbarForeground`.
- Type: Inter (bundled, SIL OFL — include its license) preserves the brand in
  artwork and the Home wordmark. iOS uses the system semantic text styles for
  navigation, controls and body content, scaling with Dynamic Type; Android keeps
  the generated Inter typography. Desktop sizes
  (`VSTokens.text*`) are the base; scale with Dynamic Type / Android font
  scale. Body text on phones uses `editor` (16/28); controls use `ui` (14/20)
  scaled up to the platform minimum touch-readable size.
- Radii: `radiusLg` for cards, `radiusControl` for buttons/fields. Spacing:
  `spacingGap` between elements, `spacingInset` for screen padding (cap at 16
  on compact widths).
- Respect reduced motion and reduced transparency.
- Native iOS presentation follows Apple's current HIG: automatic system navigation
  and tab-bar materials, platform button shapes and sheet curvature. Content can
  scroll beneath native chrome while controls remain in safe areas. iOS content
  cards and text fields use generated `iosCardRadius` and `iosFieldRadius`
  tokens (continuous 24-point and 16-point corners). The Home wordmark uses
  generated `iosHomeWordmarkSize` (28 points), scaling with Dynamic Type;
  Android retains its generated shapes. These presentation differences preserve
  the same capabilities and consent gates on both platforms.
- Licence review (both platforms) leads with a plain summary: the licence
  category, four rights rows (commercial use, generated audio, sharing, voice
  rights; each "Restricted" or "Not stated"), the licensor's notes, the credit
  and tappable https licence/source links. The raw terms, fingerprint, licence
  code and receipts sit under a collapsed "Exact terms" section. iOS shows it as a
  large, scrollable sheet with a leading Close action, an initially unchecked
  confirmation and a persistent Accept action disabled until confirmation. Closing
  never records acceptance or starts a download. On both platforms, multi-selection
  sheets/dialogs edit a draft; Cancel/dismiss discards it and Confirm applies it.

## Strings

- All user-facing text through a small `L10n.t(key, params)` that reads the
  bundled Electron catalogs: nested JSON, dotted keys, i18next `{{name}}`
  interpolation, `_one`/`_other` plurals, fallback chain
  `<locale> → <language> → en → key`. Locale chosen from the system, overridable
  in Settings using the desktop language list.
- Reuse existing keys where the meaning is identical (`common.*`, `tts.*`,
  `clone.*`, …). New mobile keys live under the `mobile.` namespace and are
  listed in `contract/strings.mobile.en.json` (single shared list — both apps
  use the same keys). If you need a key that is not there, add it to
  `contract/strings.<platform>-additions.en.json` instead; the maintainer merges
  those into the shared list and the Electron catalogs with translations.
- No hardcoded user-facing text in Swift/Kotlin. No CJK anywhere outside the
  catalogs.

## Screens (M1)

Bottom navigation: **Home**, **Voices**, **Dub**, **Dictate**, **Settings**.
Home is the default after consent. All five navigation destinations keep visible
text labels; iOS keeps the full tab bar visible while scrolling. The Home voice
cloning shortcut opens creation directly, while the Voices tab opens the library.
Before a speech model is ready, Speech pins its explicit Download action in place
of a disabled Generate button. Downloading still requires each model’s exact
licence consent. Dictate explains the model download and its size before it
invites recording. The compact hero places the wordmark and feature
summary beside the labeled contributor column; accessibility text sizes can stack them.
Both apps show larger core-maintainer portraits
above a two-row horizontal scroller of remaining contributors; tapping a core maintainer opens a scrollable
native sheet with their localized identity and website, GitHub and X links.
Profile metadata and brand artwork derive from Electron and are bundled offline.
External pages open only on an explicit link tap. It adapts Electron's dashboard with the same
bundled `electron/public/signal-field.webp` artwork, generated palettes, Inter,
card hierarchy, recent generated audio, recent dubbing projects and saved voices.
Its working shortcuts open Speech, voice cloning, Dub, Dictate, Models, Connect
and the M3 production workspaces below. Speech stays reachable from Home; it is not a sixth bottom tab. Recent
phone audio plays locally, saved phone voices open Speech with that reference
selected, and project rows open that project's outputs/resume controls. Computer
libraries are shown only from the paired host. Empty sections use localized
empty states; Home never starts downloads or external requests on its own.
Every production shortcut opens a native implementation on both platforms;
M3 documents their supported operations and execution locations.
All tabs work without pairing once on-device models are installed (see M2); the Connect flow is reachable from first run and from Settings.

1. **First run**
   - Analytics consent: same copy keys as desktop, two equal-weight Yes/No
     buttons, never default-on, dismissing = No. M1 does not transmit
     analytics; it only records the choice (desktop parity for later).
   - Connect screen.
2. **Connect / pairing**
   - Scan QR (camera) of the desktop "Network" share panel: URL
     `http(s)://<host>:<port>/?pin=<6 digits>`. Also manual entry: URL + optional
     PIN + optional API key.
   - Validate with `GET /health` (must be a VoiceStudio response).
   - PIN: send `X-OmniVoice-Pin` on every request.
   - API key: exchange once via `POST /api/auth/session` with
     `Authorization: Bearer <key>` and body `{"transport":"bearer"}`; store only
     the returned session (Keychain / Android Keystore-backed storage) and
     send it as `Authorization: Bearer <session>`; never persist the key.
     On 401 ask again. "Disconnect" calls `DELETE /api/auth/session`.
   - Saved hosts list (URL, label, last seen); one active host.
   - See `docs/api-auth.md`, `docs/sharing.md`.
3. **Speech** (TTS)
   - Text editor, voice picker (`GET /profiles`), engine/language pickers
     from `GET /engines` and backend language data, Generate → `POST /generate`.
   - Play result in-app (streamed from the host), waveform/progress scrubber,
     share/save via system share sheet. Recent generations: `GET /history`
     (star/delete).
4. **Voices**
   - List/search profiles, play preview, create a clone: record (mic) or pick
     audio file → upload as the clone endpoint expects (see OpenAPI
     `/profiles` POST), with the consent statement before cloning.
   - Delete/rename.
5. **Dub**
   - Pick video/audio from the device or paste a URL (`/dub/ingest-url`),
     upload (`/dub/upload`), choose target language(s) and voice, start,
     monitor progress (job events / polling per OpenAPI), abort, download/share
     results (`/dub/download*`). History: `/dub/history`.
   - Long jobs run on the host; the app reconnects and resumes monitoring.
     Upload continues in the background (iOS background URLSession / Android
     WorkManager). Local notification on finish if the user allowed
     notifications (no APNs/FCM).
6. **Dictate**
   - Hold/tap to record, stream to the host dictation WebSocket (ticket from
     `POST /api/auth/ws-ticket` when a session is used), show live transcript,
     copy/share. Readiness from `GET /dictation/readiness`.
7. **Settings**
   - Connection (hosts, disconnect), Appearance (mode + palette per appearance,
     live preview), Language, Notifications, Privacy (analytics choice, what
     leaves the phone: nothing but requests to the paired host), About
     (version from the root `package.json`, licenses incl. Inter, model
     licenses link to the host).

## Platform rules

- Networking: plain HTTP allowed **only** to local/private addresses
  (iOS `NSAllowsLocalNetworking` + `NSLocalNetworkUsageDescription`; Android
  `network_security_config` for loopback/RFC1918/CGNAT `100.64/10`/link-local
  and `.local`/`.ts.net`). Everything else HTTPS.
- Permissions requested only at first use, with a reason string from L10n:
  camera (QR), microphone (record/dictate), local network, notifications.
- Errors: every network/backend error maps to an actionable message (what
  happened + what to do), mirroring desktop error copy where it exists.
- Accessibility: every control labelled, Dynamic Type / font scale up to the
  largest size without clipping, minimum 44pt / 48dp touch targets, contrast
  from tokens.
- Minimum OS: iOS 17, Android API 29 (target the latest stable SDK).
- Version: read from the root `package.json` at build time (never hard-coded).

## Tests each app must have

- Token parity: every palette/role in `contract/tokens.json` resolves in the
  app's theme layer.
- L10n: interpolation, plurals, fallback chain, every key used in code exists
  in `en` (shared list + additions).
- Pairing URL parsing (QR forms, IPv4/IPv6, missing PIN), cleartext policy
  (private vs public hosts).
- API client against a mock server: health check, PIN header, session
  exchange/revoke, 401 handling, generate, dub upload + progress.
- UI smoke: launch → consent → connect (mocked) → Speech generates → play.

## M2 — on-device inference (owner-set 2026-10-18)

### Runtimes (one per capability, identical on both platforms)

| Capability | Runtime | Pin |
|---|---|---|
| TTS (preset voices), dictation, transcription | sherpa-onnx | release `v1.13.8` |
| Voice cloning | omnivoice.cpp (GGML) | commit `886fc079838ca7400cb2b42b36e2a65aa1daabe8` (same as desktop `quant_map.json`) |
| Dub translation | llama.cpp | release `b11716` |

- Native libraries are built or fetched by `mobile/native/` scripts at the pins
  above, verified by SHA-256, and never committed. Prebuilt artifacts (sherpa
  AAR, llama xcframework) are downloaded with a recorded checksum; the rest is
  built from source (iOS: CMake + Xcode toolchain, Metal where supported;
  Android: NDK `arm64-v8a`, plus `x86_64` for the emulator).
- Each app wraps the runtimes behind one platform interface
  (`LocalEngine`: `synthesize`, `clone`, `design`, `transcribe`, `streamTranscribe`,
  `translate`, all cancellable, all off the main thread, progress reported).
  Feature screens never call a runtime directly.

### Models

- `mobile/contract/models.json` is **generated** by
  `uv run python scripts/export_mobile_models.py` from
  `mobile/contract/models.source.json` (which models the phone offers: HF repo,
  pinned revision, files with SHA-256 and size, capability, languages, minimum
  RAM) joined with `docs/licensing/model-license-data.json` (licence, category,
  credit, rights holder). A phone model **must** have a registry entry; adding
  one follows `docs/licensing/model-review-workflow.md`. The contract test fails
  on drift, on a missing registry entry, or on a file without a checksum.
- Speech offers KittenTTS mini, Kokoro multi-language, Piper Lessac, Supertonic 3,
  Pocket TTS, ZipVoice and OmniVoice-GGUF (Q8_0 base / F32 codec). Piper/Supertonic use preset
  voices; Pocket/ZipVoice require a saved reference (ZipVoice also uses its
  transcript). OmniVoice also requires a saved reference in Speech; Voice Design
  is a separate explicit task. Reference-only models show guidance and disable Generate until a saved
  voice is selected. Model selection is independent of voice selection; choosing
  a preset-only engine clears an incompatible saved voice. Voice Design lists
  only engines that actually support design. Nothing is downloaded by selecting
  a model. Licence review and explicit installation still apply to every pack.
  Whisper tiny/base and streaming Zipformer provide dictation, and a small
  instruct LLM provides translation.
- OmniVoice uses the runtime author recommended Q8_0 base and native F32 codec
  (about 1.4 GB; minimum 8 GB RAM). The legacy `omnivoice-q4-k-m` identifier
  remains stable for preferences and storage. Older Q4 files are retained; use
  Models to download the new verified files. Licence terms/fingerprints are
  unchanged. Bare Speech synthesis cannot silently fall back to voice design.
- Model files normally resolve from their repository's fixed revision. Separate
  components may specify an allowlisted HTTPS `url` (fixed HF revision or the
  k2-fsa sherpa-onnx ONNX release assets), or a safe `bundled` path relative to the
  contract resources. Both retain mandatory byte counts and SHA-256 checks;
  bundled frontend data is copied only during explicit model installation and
  can finish offline. ZipVoice includes its separately pinned Vocos vocoder,
  English eSpeak files and bundled bilingual lexicon. User voice recordings are
  never included in model downloads. Pocket's conflicting conversion terms are
  disclosed conservatively as non-commercial; Piper's training-data rights remain
  unresolved in the review. Existing model fingerprints are unchanged.
- `models.json` shape (both apps decode exactly this; unknown keys ignored):

  ```json
  {"schema_version": 1, "models": [{
    "id": "kokoro-multi-int8", "name": "Kokoro", "capability": "tts | asr | asr-stream | clone | translate",
    "runtime": "sherpa-onnx | omnivoice.cpp | llama.cpp",
    "repo": "csukuangfj/kokoro-int8-multi-lang-v1_1", "revision": "<40-hex commit>",
    "files": [{"path": "model.int8.onnx", "size": 0, "sha256": "<64-hex>", "role": "model"}],
    "total_bytes": 0, "languages": ["en", "zh"], "min_ram_gb": 3,
    "licence": {"id": "apache-2.0", "category": "commercial-ok | non-commercial | conditional | unidentified",
                "requires_acceptance": false, "credit": "", "rights_holder": "",
                "terms_fingerprint": "<sha256 of the terms shown>"},
    "config": {}}]}
  ```

  `name` is the model's product name, shown instead of the id (never translated;
  what it is for comes from the app's localized capability label). The apps never
  show ids, repo names or licence codes on screens; licence details appear only
  in the acceptance sheet.
  `role` tells the runtime what a file is (e.g. `model`, `tokens`, `voices`,
  `encoder`, `decoder`, `joiner`, `espeak-data`, `tokenizer`); `config` carries
  runtime options (sample rate, speaker count, prompt template).
- **Downloads only when the user starts them**, from Hugging Face (falling back
  to `hf-mirror.com` exactly like the desktop endpoint race), resumable, Wi-Fi
  only unless the user allows mobile data, verified by SHA-256 before use,
  stored in app-private storage excluded from backups. A Models screen shows
  each model's size, languages, licence and storage, with Download, Cancel and
  Delete, and the total space used.
- **Licence acceptance matches the desktop:** a model whose licence category is
  not commercial-OK must be accepted on the phone before it is downloaded or
  loaded. Acceptance is recorded locally per model with the fingerprint of the
  exact terms shown, the app version and the time, and can be withdrawn.
- **Device fit:** each model declares minimum RAM. Models the phone can't run
  are shown disabled with the reason, never hidden and never attempted.

### Where each task runs

- Every task (Speech, clone, Dictate, Dub) has **Run on: This phone / My
  computer**. Default: This phone when the needed model is installed; otherwise
  the computer if one is paired; otherwise an inline prompt to download the
  model (with its size). The choice is remembered per task.
- With no computer paired, the app is fully usable: the Connect screen is
  optional and reachable from Settings. Tabs never require pairing.
- Voices: cloned voices made on the phone are stored on the phone (reference
  audio + metadata); computer voices stay on the computer. Each list says where
  a voice lives.

### On-device dubbing

Extract audio (AVFoundation / MediaExtractor), transcribe (sherpa Whisper),
translate per segment (llama.cpp), synthesize per segment using either the
original speaker from a source reference clip or a saved phone voice
(omnivoice.cpp), fit timing,
mix, and mux back into the video (AVAssetWriter / MediaMuxer). Long jobs run as
a foreground task with progress (Android foreground service; iOS keeps
progress visible and resumes from a saved checkpoint after interruption).

### Privacy

On-device tasks send nothing anywhere. The only network requests are the model
downloads the user starts, content reports the user sends (see Reporting
generated content) and, when paired, requests to their own computer.
Settings → Privacy states this.

### Tests (in addition to M1)

- Models contract: generated catalogue matches its sources; every file pinned
  and checksummed; every model has a registry entry and correct licence gate.
- Download manager against a mock server: resume, checksum failure, Wi-Fi-only
  rule, cancel, delete, low storage.
- Licence gate: gated model can't download or load before acceptance;
  withdrawal blocks it again.
- Real inference smoke test in CI with the smallest models (cached): TTS
  produces audio and ASR transcribes it back (iOS on the macOS runner via
  VoiceStudioKit; Android on an `x86_64` emulator).
- Routing: phone / computer / download prompt choices, and offline use with no
  computer paired.

## Reporting generated content

Google Play's AI-generated content policy requires an in-app way to report
offensive output; both apps ship it. The server is VoiceStudio's app API
(`POST /v1/content-reports`, spec `api/openapi/v1.yaml` in the vssaas
repository). It needs no sign-in and accepts no audio or identifiers.

- **Where:** every generated result the app shows has a **Report** action,
  whether it ran on the phone or the paired computer: a Speech result and its
  recent takes (`task: speak`), the cloned-voice result on Voices
  (`task: clone`) and a finished dub (`task: dub`).
- **Sheet:** title, a required reason chosen from five (`sexual`, `hateful`,
  `impersonation`, `misleading`, `other`; none preselected, Send disabled until
  one is picked), an optional note (at most 1000 characters), an **Include the
  text I entered** checkbox (unchecked, shown only when the result has text the
  user typed), the disclosure of what is sent, and Send / Cancel.
- **Nothing is sent until Send.** The request carries only the fields below; an
  unticked checkbox omits `prompt_text`, and empty optional fields are omitted.

  | Field | Value |
  |---|---|
  | `reason` | the chosen reason |
  | `note` | trimmed note, if any |
  | `prompt_text` | the typed text (first 5000 characters), only when ticked |
  | `platform` | `android` / `ios` |
  | `app_version` | root `package.json` version |
  | `task` | `speak` / `clone` / `dub` |
  | `model_id` | phone: the `repo` from `models.json` of the model that made it (Speech: the TTS or clone model; a cloned voice: the `clone` model that generated its saved preview (legacy voices can retain their ASR attribution); a dub: its pinned `clone` model; results saved before this feature: `unknown`); computer: the engine id the host reported, else `unknown` (characters outside `0-9A-Za-z._/:-` removed, at most 128) |
  | `model_revision` | phone only: the model's `revision` |
  | `runs_on` | `phone` / `computer` |

- **Request:** `POST <base>/v1/content-reports`, `Content-Type:
  application/json`, `Idempotency-Key` and no `Authorization`. Every build,
  debug included, uses `https://appapi.voicestudio.sh` unless
  `VOICESTUDIO_CONTENT_REPORTS_URL` (an https URL, from the environment or the
  git-ignored `mobile/.env`; see `mobile/.env.example`) points it elsewhere, such
  as `https://dev-appapi.voicestudio.sh` for testing. The key (32 characters of
  `A-Za-z0-9`) is made when the sheet opens and reused when the user retries,
  so a retried report is stored once.
- **Responses:**

  | Status | App |
  |---|---|
  | 201 | Close the sheet, show `mobile.report.sent`, discard the draft |
  | 409 | Make a new key and send once more automatically |
  | 429 | Keep the sheet and draft, show `mobile.report.rate_limited` |
  | 400 | Show `mobile.report.invalid`; no retry (an app bug) |
  | anything else, or no connection | Keep the draft, show `mobile.report.failed` with Retry |

- **Draft:** kept in memory per result until it is sent, so closing and
  reopening the sheet keeps the reason, note and checkbox.
- **Tests:** against a mock server: exact body (no extra fields, prompt text
  only when ticked, optional fields omitted when empty), headers (key format,
  no `Authorization`), the same key on Retry, a new key after 409, 429 and 400
  handling, and the production base URL unless a build overrides it.


## M3 — native production workspaces

Home exposes Voice Design, Stories, Audiobook, Batch dubbing, Gallery, Tools and
Workflows on both platforms. These are native screens; no embedded desktop UI.

- **Voice Design** uses the installed OmniVoice model through `LocalEngine.design`,
  the existing clone-model licence/fit gate and the shared native inference
  lease. Gender, age, pitch and style options are generated from Electron's
  `CATEGORIES` by `bun scripts/export-mobile-design.ts` into `design.json`;
  both resource pipelines bundle it. Seed is stable until New seed. Saving a
  generated sample writes marked reference audio, its exact text, model identity,
  traits and seed atomically. It becomes an ordinary phone reference voice.
  A saved reference must be 0.1–30 seconds; longer samples remain shareable.
- **Stories / Audiobook** share `ProductionProject`, including manuscript,
  per-character casting, pronunciation, speed and pause controls. Bounded local
  EPUB/text-PDF/TXT import never executes embedded content or extracts ZIP paths.
  Unencrypted files are capped at 64 MB; scanned PDFs fail without readable text.
  Content-addressed segment audio includes voice/model/settings; changing one
  line reuses unaffected audio. Disk-streamed marked WAV and chapter outputs
  export as WAV, MP3, AAC/M4A and chaptered M4B (up to 255 chapters). The same
  pinned, source-built LAME encoder ships on both; cancellation and atomic
  replacement preserve earlier results. Speech also exports WAV/M4A/MP3.
- **Batch dubbing** publishes requests before enqueueing. Native durable queues
  resume checkpointed work; cancellation preserves completed segments. No watch
  folders or automatic imports. Host/source changes invalidate stale results.
- **Dub editor** retains stable segment identities, per-language output hashes
  and per-segment audio caches. Text/timing/voice edits, split/merge, undo/redo,
  bounded SRT/VTT import/export and original/generated waveforms ship together.
  Persisted segment gain/mute applies only during preview/mixing, invalidating
  output fingerprints while keeping synthesized PCM caches valid.
  Previous outputs remain until replacements succeed. Stale generated previews
  are unavailable; source/reference media stay until explicit deletion. Manual
  speaker labels and cast-wide voice assignment retain per-line overrides and
  deduplicate reference PCM. Styled/dual ASS uses line timings. Clip and audio
  packages verify cached render hashes and exclude voice references; original
  audio is explicitly unseparated. Native encoded-video mux preserves multiple
  language/default/alternate tracks. ZIP exports cap total bytes at 1 GiB and
  each file at 512 MiB.
- **Advanced paired dubbing** reuses installed-only diarization and existing host
  SSE/task receipts. Explicit Detect requests `reanalyze_speakers=true` to refresh
  labels on completed transcripts while preserving cue IDs, text, translations,
  timing and explicit voice choices; ordinary reconnects replay the saved result.
  Analysis and regeneration retain per-cue `gain`, `speed`, `direction`, `instruct`
  and `effect_preset`; omitted legacy values retain the backend defaults.
  Recasting translates fresh source cues on the same approved
  provider before regeneration; resumed events validate original source text.
  Host export supports selected/default/original tracks, separated background,
  subtitle burn, dual/karaoke captions and existing audio/subtitle/package formats.
  Acoustic diarization, isolated background separation and subtitle burn remain
  explicit computer tasks.
- **Editable project handoff** uses a version-1 `.vsproject` stored ZIP with a
  SHA-256 manifest, canonical manuscript/timeline, assets, voice metadata and
  preserved foreign settings/caches. Both phones and Electron import new IDs;
  canonical phone edits override preserved desktop snapshots. Story chapter
  headings survive both directions without becoming spoken lines; dub targets
  include saved translation languages even before those tracks are rendered.
  Voice/source conditioning requires explicit permission; credentials, licence receipts and
  ownership verification are never restored. Bounded extraction rejects unsafe
  paths, links, duplicate entries, unsupported versions and altered content.
  Imports roll back failed writes; disconnected host imports finish their
  transaction and remain available in the paired inbox. Completed outgoing host
  archives expire after 24 hours or when pruned to the newest 20 within 2 GiB;
  active downloads and imported projects/assets are never pruned.
- **Tools** provides offline ASR/re-synthesis conversion of clips up to 30 seconds,
  optional pitch-preserving duration fitting, and A/B synthesis of two phone
  voices. Conversion aborts on an empty transcript before synthesis; decoded
  scratch PCM is removed on success, failure or cancellation. Each result has
  playback, system sharing and Report with the actual generating model. Inline
  conversion recording requires microphone permission and consent, stops at
  29 seconds and releases capture on lifecycle stop.
- **Audio editor** uses bounded disk passes (up to four hours) for trim, fades,
  gain, actual peak normalization with a −50 dBFS silence guard, and a noise gate.
  It preserves source media and synthetic provenance, supports WAV/M4A/MP3,
  and never loads a model or marks natural-only DSP as synthetic.
- **Gallery** opens the public `debpalash/omnivoice-gallery` community catalogue
  directly on either phone, without pairing or installed models. Both builds bundle
  the same `community-gallery.json` snapshot (upstream manifest, 2026-10-26, retaining
  per-item author/licence); refresh it from the repository when updating the seed.
  Opening Gallery may refresh the jsDelivr manifest, never app startup. A validated
  disk cache or bundled seed stays available offline. Search, pack and language
  filters show named designed recipes and recorded voices with attribution.
  Use voice opens a recipe in native Voice Design with its actual instruction,
  script and name; generation still requires explicit model download/licence consent.
  Recordings download only on Play or an explicitly confirmed import. HTTPS hosts
  and redirects are allowlisted, manifests bounded to 4 MiB and recordings to 32 MiB;
  SHA-256 is verified before caching, playback or import. Import requires voice
  permission, a nonempty transcript and reference up to 30 seconds. It preserves
  author/licence attribution and synthetic provenance without inference or ASR.
  This phone retains bundled engine presets with model/speaker selection in Speech.
  My computer retains paired community browsing and stored voice-bundle copying;
  switching hosts discards those host results. All generation stays on the chosen
  device and all gallery downloads are initiated by entering Gallery or its controls.
- **Workflows** retain ordered legacy recipes and support typed acyclic graphs
  with yes/no contains/equals/starts/ends conditions and compatible branch joins.
  Transcription, translation, speech, ASR/re-synthesis conversion and normalization
  execute with bounded retries. Input/model/settings hashes bind checkpoints and
  saved run paths. Desktop version-1 workflow JSON import/export validates nodes;
  agent/call/unknown nodes, cycles, unreachable paths, incompatible types and
  multiple input batches fail explicitly. Audio is reattached and voices rebound
  locally. Conversion is limited to 30 seconds; templates remain local.
- **Transcriptions** saves local and paired live dictation into app-private history.
  Search, original-preserving edits, copy, delete, TXT/JSON and timed SRT/VTT
  exports are available on both. File transcription accepts source files up to 1 GiB and decodes up to four hours
  to disk and recognizes bounded 20-second chunks with absolute cue offsets.
  Atomic per-chunk checkpoints bind source SHA-256 and the exact installed model
  pin; cancellation retains completed chunks. Completion removes scratch PCM.
  Edited text cannot export stale subtitle timings until the original is restored.
  Android uses a foreground worker; iOS uses the shared CPU job owner and resumes
  saved records after restart. Import never downloads a model.
- **Jobs** live beyond screen navigation. Android foreground workers expose
  progress/cancellation. iOS requests user-started continued processing on iOS
  26+, including supported GPU continuation with the signed GPU entitlement
  and an actual OS grant. A user-enabled CPU fallback cancels/checkpoints,
  awaits native-lease cleanup, then reruns the same immutable operation under
  task-scoped CPU selection. No process-global backend environment is changed;
  foreground Metal remains the default. Unsupported/denied grants and older OS
  limits retain explicit resume. Thermal critical state and critically low
  battery pause work. Sustained jobs cover dubs, narration, workflows and file transcription;
  interactive tools remain screen-owned. Paused book/workflow recipes may resume
  only when their full draft and model configuration still match; edits discard
  stale resume closures without replacing the newer draft.
- **Cloning** requires separate ASR and cloning models. ASR alone cannot create a
  cloned voice. Publication follows successful marked cloned-preview synthesis;
  original reference WAV/PCM stays separate. Failed/canceled synthesis publishes
  no voice. Legacy voices without generated previews retain their old playback.
- **Calls** run on the paired computer. Only an authenticated operator session
  or API key plus the computer's off-by-default `allow_remote_control` setting
  enables the desktop-mode exception. PIN/trusted-network callers never gain
  call control. `/calls/settings` keeps its strict admin gate; remote callers
  cannot opt themselves in on desktop-mode hosts. Existing server-mode operator
  access is unchanged. Explicit confirmation precedes outbound dialing.
- **Phone API / MCP** is explicitly started, foreground-only and loopback-bound
  by default, with optional LAN binding. A random ephemeral bearer authenticates
  all endpoints. Strict bounded HTTP framing (16 KiB headers, 2 MiB bodies),
  Host/Origin validation, one active model operation and no arbitrary paths,
  URLs, voices or downloads constrain access. Enabled installed models alone
  are advertised. Preset/saved-voice speech returns marked WAV/MP3; opt-in ASR
  accepts base64 PCM16 mono WAV (8–24 kHz, at most 30 seconds), and opt-in text
  translation accepts only selected-model language codes. Stateless JSON MCP
  2025-11-25 supports initialize, ping, tools/list and tools/call with enabled
  speak/transcribe/translate tools. Closing/backgrounding cancels all requests.
- **System integration** accepts shared text/audio/video into a review screen.
  Widgets/shortcuts open workspaces without automatic inference or microphone
  access. Voice-design recipes can be restored into their editor.

Generated speech in every workspace uses the existing synthetic-audio marking
chokepoint. Report reuses the existing `speak` task; mixed-model books report
`unknown` rather than incorrectly attributing every voice to one model. No new
model downloads, gallery imports, calls or integrations start from Home.
