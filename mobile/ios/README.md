# VoiceStudio for iOS

Native SwiftUI app for iOS 17+, iPhone and iPad. Speech, voice cloning, Dictate and dubbing run downloaded models on the phone, offline. Pairing a VoiceStudio computer is optional and adds its engines. Every task remembers its own phone/computer choice; the segmented control appears only while paired. All five tabs work without pairing. Settings contains Connect and Models.
## Build and checks

Swift 6 Command Line Tools, Python 3.9+ and CMake run the macOS package checks. XcodeGen 2.46 generates the app project. Full Xcode with the iOS SDK is required for the app, native xcframeworks and UI tests. Device signing uses your own development team. The app and Share extension need the `group.sh.voicestudio.mobile` App Group enabled in Signing & Capabilities; use a group registered to your team when customizing the bundle IDs. Both targets must use the same group in their entitlements and `PhoneInbox`/`ShareViewController`. Background GPU continuation also needs the `com.apple.developer.background-tasks.continued-processing.gpu` entitlement in the signed app profile on supported iOS26+ hardware; scheduler approval, not a source entitlement file, determines availability. CPU fallback is explicit and preserves checkpoints. The root `package.json` supplies the app version; there is no mobile version mirror.

Run from the repository root:

```sh
mobile/ios/scripts/check-swift.sh build
mobile/ios/scripts/check-swift.sh test
mobile/ios/scripts/check-platform.sh
python3 mobile/ios/scripts/prepare_resources.py \
  --output mobile/ios/.generated/bundle \
  --info-output mobile/ios/.generated/Info.plist
python3 mobile/ios/scripts/test_resources.py
swiftc -frontend -parse mobile/ios/VoiceStudio/App/*.swift \
  mobile/ios/VoiceStudio/Features/*.swift mobile/ios/VoiceStudio/Platform/*.swift \
  mobile/ios/VoiceStudioUITests/*.swift
```

`check-swift.sh` keeps build/module caches inside `mobile/ios/` and supplies the installed Testing.framework on CLT-only installations. Package tests do not download models. The optional `VOICESTUDIO_SWIFT_SCRATCH` selects a separate scratch directory. `check-platform.sh` type-checks the portable host media loader and smoke transport against macOS. The phone model coordinator now owns UIKit background jobs, so the full iOS app build checks it together with `PhoneJobs`; the macOS check does not substitute for that build.

Explicit preparation and real inference (model terms are printed; test acceptance stays in the isolated fixture store):

```sh
mobile/native/ios/build.sh macos-runtimes
python3 mobile/ios/scripts/fetch-smoke-models.py --accept-test-licences
mobile/ios/scripts/real-inference.sh
mobile/ios/scripts/native-load-test.sh
# Cloning, translation, phone voice creation and complete phone audio dubbing:
python3 mobile/ios/scripts/fetch-smoke-models.py --accept-test-licences --extended
mobile/ios/scripts/auxiliary-inference.sh
```

The smoke fetcher verifies every file's size and SHA-256 from the actual shared catalogue, then stores files under ignored `.cache/inference-models/`. Inference tests themselves use only these local files. Kitten produces actual audio, Whisper transcribes it back, and Zipformer emits incremental and final text. The same smoke cancels real TTS after observing native progress. The auxiliary script runs three real native workflows sequentially to limit peak memory: OmniVoice/Qwen adapters, phone reference creation through ASR followed by cloned Speech, and the full Whisper → Qwen → OmniVoice audio dub pipeline with verified export and completed-checkpoint resume. These tests do not grant acceptance in the app's model store.

With full Xcode, prepare the native frameworks before generating or building the app:

```sh
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
mobile/ios/scripts/check-swift.sh test
mobile/native/ios/build.sh ios
xcodegen generate --spec mobile/ios/VoiceStudio/project.yml
xcodebuild -project mobile/ios/VoiceStudio/VoiceStudio.xcodeproj \
  -scheme VoiceStudio -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
xcodebuild -project mobile/ios/VoiceStudio/VoiceStudio.xcodeproj \
  -scheme VoiceStudio -destination 'platform=iOS Simulator,name=iPhone 16' test
```

Choose an installed simulator name. The native build verifies pinned artifacts, builds missing simulator slices and exposes the frameworks expected by `project.yml`; see `mobile/native/ios/README.md` for integrity details and transitive checksum limits. No native libraries or model binaries are committed.

## Phone inference and models

`VoiceStudioKit/LocalEngine` is the single feature-facing interface: synthesize, clone, transcribe, streamTranscribe and translate. C runtime handles remain on detached workers; an atomic cancellation token crosses tasks. The process-wide native permit and model file lease remain owned by that worker until native cleanup finishes, including after cancellation and for streaming. TTS uses sherpa Kitten/Kokoro/Piper/Supertonic; Pocket TTS and ZipVoice use sherpa reference cloning, offline ASR uses Whisper, and streaming ASR uses the catalogue's Zipformer family. Streaming preserves endpoint finals, produces a final session transcript and rejects sample-rate changes. Whisper handles long input in 20-second windows while reusing the recognizer. Its native decode has no abort callback: cancellation is checked between windows and the current decode must return before cleanup. TTS and OmniVoice cancel through their native callbacks; llama checks between tokens and through its CPU abort callback. Progress reports loading/running/finished; native APIs without fractional callbacks remain indeterminate during the current step. Every locally generated WAV passes `markSynthetic`, which adds a synthetic-audio INFO comment.

Speech, clone, Dictate and Dub remember independent This phone / Computer choices. Without a choice, a compatible installed and accepted phone model wins; otherwise a paired computer wins; otherwise a compact download card offers Get and links to Models. A remembered computer choice falls back to the phone or Models prompt while unpaired, matching Android, and applies again after pairing. Speech offers three compact pickers (voice, model, language), a text box, Generate and the existing playback, waveform, seeking, sharing and saving controls. Its phone voice picker includes presets and saved references; reference voices select OmniVoice rather than preset TTS. Phone generations persist locally and can be played or deleted in Recent. Dictate captures mono PCM16 at 16 kHz and sends it directly to `LocalEngine` on the phone route. Neither phone path requires a computer or makes a network request.

Models decodes the generated `mobile/contract/models.json`, bundled at build time alongside the locale catalogs. Unknown fields are ignored. Missing or blank product names, invalid revisions, hashes, unsafe file paths, sizes, duplicate IDs and runtime/capability mismatches are rejected. Cards show product name, size, localized capability and one Download/Cancel/Delete action. Feature screens and model cards hide IDs, repository names and licence codes; licence details are in the acceptance sheet. Languages, licence terms and per-model storage are in the licence sheet. Unsupported RAM requirements stay visible with an explanation and cannot download or load. Models has Download, Cancel, Delete, a total storage counter and an explicit mobile-data toggle.

Downloads start only from a user action. Four-MiB HTTP ranges persist across cancellation and relaunch; servers ignoring ranges can return a complete file. Hugging Face is attempted first, then hf-mirror.com. A dedicated cookie/credential-free session permits only HTTPS HF/mirror delivery domains (including signed HF CDN redirects); host credentials and referrers are never forwarded. Wi-Fi-only is enforced by both the local path check and URLSession's cellular/expensive-network policy unless mobile data was allowed. Files stay under private Application Support, excluded from backup. Each file is size/SHA-256 verified before installation. Native loads reuse per-file verification stamps containing size, modification time and catalogue identity; changed stamps or a native failure force fresh hashing. A relaunch verifies files on their first load. Resume checks storage only for bytes still needed in the current file, skipping verified completed files. Active leases prevent delete/replacement during inference. Downloads interrupted by suspension resume from persisted ranges when the user starts them again; continuous background model downloading is not promised.

Every non-commercial, conditional, unidentified or explicitly gated model requires acceptance before download and load. The phone shows all supplied licence metadata and full catalogue terms, records the supplied terms fingerprint, actual app version and time, and retains the latest 20 actions locally. Changed terms block loading again; withdrawal immediately blocks new use and downloads. Acceptance is independent of the computer. Delete removes weights and partial files, preserving licence history.

OmniVoice's `tokenizer` role is its audio codec. The clone adapter takes mono 24-kHz reference audio plus its transcript. Translation uses the catalogue's prompt template and a bounded 2,048-token context. All features call this boundary; screens never invoke C runtimes directly.

## Phone voices and dubbing

Voices lists phone and computer voices together, with initials, a location badge, preview and rename/delete actions. The toolbar plus opens cloning: name, consent, record or pick a sample, Create clone. The consent details are behind an info button. Phone creation requires a downloaded, accepted Whisper model; it extracts mono 24-kHz reference audio (0.1–30 seconds), transcribes it and atomically publishes reference PCM/WAV plus transcript, creation time and consent time in private, backup-excluded storage. Empty speech and cancelled creation never publish a voice. Recording stops natively at 29 seconds, keeping the sample within the 30-second cloning limit even if UI timers are delayed. Decoding caps accepted duration-rounding tolerance to exactly 30 seconds. OmniVoice must be downloaded and accepted before Speech can synthesize with that reference. Renaming/deleting a phone voice never changes a computer voice.

Phone Dub follows Source → Languages → Voice. Pick a local audio/video file, choose up to 20 target languages and either the original speaker or a saved phone voice, give voice permission and tap Dub. More contains model choices and audio-only export. Pasted links remain a computer workflow: phone processing never fetches media from a URL. The three required models (Whisper, Qwen translation and OmniVoice) must be installed, fit the device and have current licence acceptance.

Extraction uses AVAssetReader with 24-kHz mono PCM, preserving gaps and soundtrack offsets. Uncompressed audio files use AVAudioFile/AVAudioConverter to stream and resample directly without a media-reader service. A disk-backed pipeline bounds native ASR input to 20 seconds; sherpa token timestamps define each window's speech envelope (falling back to the window duration when unavailable), matching Android. Each segment is translated with the catalogue prompt and cloned from at most eight seconds cut from the source; the reference clip gets its own matching ASR transcript, falling back to the segment transcript if the clip is quiet. Empty saved reference transcripts are repaired during resume. A chosen saved voice supplies its reference and transcript instead. Short speech is padded, long speech is compressed by waveform overlap-add, segments are mixed at their timestamps and clipped to the original duration. Tiny windows use interpolation. WAV output passes `markSynthetic`; video uses AVAssetWriter to retain the original video/rotation and replace the soundtrack with AAC, with synthetic provenance in MP4 metadata. The original music is not retained as a separate stem.

Progress cards show stage, percent and Cancel. Backgrounding cancels ongoing work; Whisper's current native decode must finish before cancellation returns. Sources, exact model identities (including terms/configuration), original PCM, segment translations, reference transcripts, render hashes and outputs are checkpointed atomically under private storage. Resume on a pending card continues after interruption/relaunch without translating or mixing completed segments twice. Corrupt cached renders are rebuilt; corrupt completed outputs and changed catalogue identities are blocked. Licence permission is rechecked between stages and by each native load. Multiple target outputs commit separately. Phone history plays video/audio, shares, saves via the system document picker and deletes completed jobs. Completed phone dubs use the same opt-in local notifications setting as computer dubs. Pending jobs can also be deleted when idle. Deleting a job removes its source, checkpoints and outputs; files are retained until explicit deletion. Media is bounded to four hours, references to 30 seconds, and ASR state to 10,000 segments.

The screen layout uses short labels and semantic icons with one primary action, compact history rows, a round dictation microphone, expandable manual pairing and grouped Settings rows. Each feature has one compact missing-model card: product name and size for one model, or feature, count and combined size for several. Get starts the required downloads and presents separate unchecked acceptance sheets for every gated model; declining or dismissing stops the remaining queue. Already installed models are not downloaded again after acceptance. Tap the card label or Settings → Models to manage models. Clone model selection is under More. Pickers mark their current selection; palettes show token-based color swatches and normal text colors. Dictate copy/share controls sit above the tab bar in a bottom safe-area inset. Explanations and privacy details are in info sheets; full legal terms remain visible in licence sheets.

The app icon and launch logo are generated from `docs/logo.png` by `scripts/generate-brand-assets.swift` through `prepare_resources.py`. The icon is opaque; its canvas and the light/dark launch background come from shared tokens. XcodeGen prepares the ignored asset catalog before discovering resources, and each app build refreshes it. No generated artwork is committed. First-run consent says the choice is saved on this phone and sends nothing.

## Computer workflows

Connect accepts the desktop Network QR or manual URL/PIN/API key. Health validation rejects unrelated services. API keys are exchanged once for bearer sessions and never saved. Keychain uses `AfterFirstUnlockThisDeviceOnly`; host metadata and job checkpoints use local preferences. PIN-only pairing authorizes HTTP consumption, not remote dictation. Disconnect revokes bearer sessions and clears local credentials even when the host is unavailable. Failed pairing preserves the current host; host changes cancel old operations and clear host-scoped media/data. Redirects are rejected for every paired-host request. Public hosts require HTTPS and system certificate validation; private/LAN/Tailnet hosts may use HTTP.

Computer Speech retains engine/language/profile choices, generation, authenticated playback, waveform/seeking, sharing/saving and history star/delete. Voices retains search, previews, consent-gated recording/file cloning, rename and delete. Dub retains device file/URL ingest, multiple target tracks, voice selection, persisted upload/job monitoring, abort/history/download/share and completion notifications. Computer Dictate uses authenticated WebSocket tickets, PCM16/16 kHz and EOF finalization. Remote or undiscoverable LLM translation destinations require equal-weight consent per host/job/provider; only verified host-loopback endpoints avoid this prompt.

Background URLSession persists computer uploads and completion receipts through relaunch. A refused background session falls back to a foreground transfer with retained staging; that fallback needs app runtime. BGProcessing monitoring is opportunistic and resumes at the next foreground/granted runtime; there is no push relay or guarantee while force-quit. Original media filenames persist through picker display, multipart staging, checkpoints and history. The host pipeline follows upload/ingest → prep events → transcribe → translate → generate → render events; backend task IDs and dub media job IDs are distinct. A crash between host acceptance and client checkpoint persistence can require host inspection before retrying because render has no client idempotency key.

## Resources, privacy and accessibility

Theme roles are generated from Electron; never edit `Tokens.generated.swift`. The maintainer regenerates shared inputs with `bun scripts/export-mobile-tokens.ts`, `uv run python scripts/export_openapi.py` and `uv run python scripts/export_mobile_models.py`. These affect both platforms and are not iOS build steps. Resource preparation copies the actual Electron catalogs, shared/platform mobile additions, desktop language catalog and models catalogue. On-device screens use the canonical `mobile/contract/strings.mobile.en.json` keys; iOS needs no platform additions. Active-work errors reuse the desktop wait message. The microphone permission prompt combines the desktop microphone reason with the shared phone/computer privacy notice. Licence codes, categories, credits, terms and fingerprints appear only in the licence sheet, matching Android. Localization supports interpolation, plurals, language/English fallback and Chinese script/region selection. Package checks cover source key availability and reject platform overrides of shared strings.

Settings, appearance and language work without pairing. Permissions are requested at first use. No analytics SDK, push service, Bonjour discovery, account or crash reporter is linked; analytics consent records only a local preference. On-device tasks send nothing; networking is limited to explicitly started model downloads and paired-host requests. Inter and the app/runtime licences are shown under About. Runtime notices include eSpeak NG (GPL-3.0) with pinned corresponding-source references, piper-phonemize, kaldi-native-fbank/KissFFT, kaldi-decoder/kaldifst/OpenFST/Eigen, simple-sentencepiece, JSON, hclust-cpp, ONNX Runtime, sherpa-onnx, OmniVoice and both ggml copies, and llama.cpp. These checked-in legal texts are bundled even without a local native build; resource checks verify their manifest and runtime pins. Compact error banners and Connect show a short heading plus the specific localized cause/recovery instruction. Corrupt voice, generation and dub records are skipped individually; model readiness refreshes independently. All colors come from theme tokens; controls use Dynamic Type, minimum 44-point targets, scrollable layouts and localized accessibility labels.

## Verification limits and interpretations

To exercise the linked iOS frameworks (including Metal), boot an isolated simulator,
provision the test models as above, then run with full Xcode selected:

```sh
python3 mobile/ios/scripts/simulator-inference.py --device SIMULATOR_UDID
```

The `VoiceStudioNative` scheme hosts the package tests inside the app. The script
runs runtime loading, video encoding/muxing, speech/recognition, cloning/translation,
saved phone voices and resumable dubbing sequentially, and rejects skipped or empty
test results. It never downloads models or accepts licences. `--test NAME` selects
one workflow; result bundles remain under the selected derived-data directory.
Simulator fixtures use the checkout's cached models; this is not a physical-device
test harness. The video regression verifies rotation, duration, playable AAC and
the synthetic-audio metadata using the platform's real encoder and decoder.

All six linked-framework workflows pass on iOS 27.1 with zero skips: native loading,
video export, Kitten/Whisper/Zipformer inference and cancellation, OmniVoice cloning
with French translation, persisted phone voices and cloned Speech, and complete
audio dubbing with checkpoint reuse. OmniVoice executes on Metal; llama reports zero offloaded GPU
layers and uses its CPU path in this simulator run. These results are separate from the macOS
inference runs below and do not establish physical-phone memory or thermal limits.

Local validation on 2026-10-18 passes the Swift 6 package suite (optional inference tests run separately), resource checks, pinned iOS xcframework builds, SwiftUI compilation, the simulator app build, and the unsigned iPhone Release build. Shared contract, theme-token and contact-email checks also pass. Separate opt-in runs exercise all three actual native libraries, Kitten/Whisper/Zipformer inference, phone reference creation and cloned Speech, and the complete audio dub workflow with native translation/cloning and checkpoint reuse. The complete native audio dub smoke passes its French-output, marked export and checkpoint-reuse assertions. Standalone translation, voice creation and cloned Speech pass. The tiny Whisper model can misrecognize the short synthetic source; these tests verify execution and pipeline completion rather than transcription accuracy. The tiny Zipformer recognizes the ending phrase of the synthetic smoke sentence; this is an inference check, not a transcription-accuracy claim.

`VoiceStudioUITests/PhoneScreens.swift` covers unpaired screens, compact download cards, product names, licence consent, picker selection, dictation action placement and collapsed details; `ScreenshotTour` captures phone and computer screens plus palette/language pickers as retained Xcode test attachments. Validation on 2026-10-19 under full Xcode exercises 20 UI scenarios on the iPhone 17 Pro simulator with iOS 26.5 across a full run and targeted reruns, including Home navigation, collapsed-tab expansion, dark/light screenshots, destructive phone-history actions and remembered Clone routing. Targeted presentation checks also pass on iOS 27.1. The 116-test Kit suite also passes, with five opt-in inference scenarios skipped in that run. The tests cover consent, optional pairing, generation/playback, phone download and licence gates, voice/dub screens, keyboard handling, search, and theme selection. They support both bottom tabs and adaptive side navigation. Download cards preserve separate accessibility identities for opening Models and starting a download. Physical-device model RAM/thermal behavior, download/cellular transitions, microphone/local-network/QR permissions, VoiceOver across all screens, background uploads and real-host end-to-end workflows still require device validation. A signed development Release build was installed and launched on the connected iPhone running iOS 27.0.1 on 2026-10-19, with compact leading tab titles and Settings/Dictate visually checked. macOS inference does not prove iOS execution.

Contract interpretations: Get starts a queue with separate per-model acceptance; declining or dismissing stops the unstarted remainder while already authorized transfers continue. The compact card links to Models through its label. The optional catalogue `licence.terms` contains the full disclosure and is displayed when supplied; the fingerprint remains catalogue-authoritative. HF's signed delivery CDNs are allowed only within its HF-owned delivery domains, because normal model resolve URLs redirect there. Downloads resume at verified range boundaries rather than URLSession opaque resume blobs. The pinned llama prebuilt lacks a simulator slice, so that slice is built from the same pinned source. Missing transitive SHA-256 fields in shared pins are documented in the native README. For dubbing, “segments + timestamps” means one timed speech envelope per bounded Whisper window, matching Android; it does not imply diarization. A remembered computer route falls back while unpaired because its segmented control is hidden. A cancelled dub stays resumable; deletion is explicit. iOS background resume is a user-started Resume after foregrounding, not guaranteed background compute. The real iOS video regression verifies AVC/AAC muxing and compressed-source extraction; physical-device codec behavior remains a separate check.

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

## Native iOS presentation

The app supports iOS 17 and later, using system navigation and tab-bar materials, semantic system
text styles, native capsule buttons, grouped Settings/Models forms and platform
sheet geometry. The Home wordmark and bundled Electron artwork retain VoiceStudio's
identity and generated palette choices. Content scrolls behind native chrome;
All five tabs use compact leading navigation titles, with Home aligned opposite
Projects, and a smaller, Dynamic Type-aware Home wordmark.
Speech and Dictate keep their main actions inside the bottom safe area. Clone
creation and QR scanning use full-screen workspaces.

Voices presents the library with one creation action; search appears when voices
exist, and model setup belongs to the creation flow. Clone groups related inputs into
native form sections. Dub uses one source picker and a compact group for target
languages, voice and permission, with advanced settings collapsed and one standard
native Start dubbing action. Speech uses labeled voice/model/language rows with
readable values and an overlaid editor placeholder, and Dictate groups recording,
copying and sharing in one bottom control row. Model download notices use compact
name/size summaries. Clone remembers its phone/computer choice between presentations.
Newer native toolbar and tab-bar APIs are guarded at both compiler and runtime
boundaries so older supported Xcode SDKs can still build the app.

Model licence review keeps the exact terms readable in a large sheet. Technical
fingerprints are expandable, confirmation starts unchecked and Accept stays
visible but disabled until confirmation. Close never accepts or starts a download.
Language pickers have Cancel/Confirm actions and discard unconfirmed drafts.
Large Dynamic Type, native reduced-transparency behavior and reduced-motion
recording feedback are supported.
Newer system materials follow the installed iOS version; the full labeled tab bar
stays visible while scrolling on iOS 26 and later. Content radii and the Home wordmark size are exported
from Electron's theme alongside the shared palette. Smoke networking and credentials
are compiled only into Debug builds for UI automation.

References: [Apple's Liquid Glass adoption guide](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass),
[Sheets](https://developer.apple.com/design/human-interface-guidelines/sheets),
[Layout](https://developer.apple.com/design/human-interface-guidelines/layout) and
[Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility).

## Home and Dub

Home leads with the "VoiceStudio.sh Open Source" title and Electron's tapered cluster of
GitHub contributor avatars (more than 10 commits, ending with "All"). The avatars are
bundled in `Resources/Contributors.xcassets`, mirror
`electron/src/renderer/src/features/home/home-contributors-data.ts` (guarded by
`tests/test_mobile_contract.py`), are regenerated with Electron's by
`scripts/update_contributors.py` and are never fetched at runtime; tapping one opens the
profile in the browser. Dub is one big source card with an animated vector mark
(`DubGlyph`, static under Reduce Motion), language and voice tiles, a consent row and live
job cards. The bottom dock keeps visible tab names alongside its icons and exposes
the same names to accessibility.

The phone screenshot tour scopes Close to each native sheet and waits for the
sheet to disappear before interacting with the underlying screen. Settings rows
can remain in the accessibility hierarchy while a picker still covers them.
Settings navigation also waits for the outgoing Models navigation bar to disappear,
reveals each destination row, and waits for the destination to become hittable
before capturing or interacting with it. A row's existence alone does not mark a
completed navigation transition. Models waits for its asynchronously loaded
licence control, rather than only the navigation bar.
UI-test setup also waits for the first-run No thanks choice to become hittable
and disappear after tapping it; Home's presence behind the consent sheet does
not establish that consent has finished.
The combined CI job allows 90 minutes for cold builds and validation; individual
UI-test readiness deadlines remain bounded separately.
The keyboard smoke waits for the shared Done control to become hittable before
its single tap, then checks that the keyboard disappears within the existing
deadline. This readiness check does not establish a product responder-routing
defect.

## Live previews

`App/Previews.swift` (debug builds only) adds Xcode canvas previews for Home, Dub (dark, light and large
text), Models, the licence sheet and the dub glyph. They run the same offline mock host as the UI tests
(`AppModel(mock: true)`), so screens render with realistic data and update as you edit. Open a view file
and press Option-Command-Return; Run (Command-R) is still a full rebuild.

Home identifies debpalash and velixio with a small core-maintainer star on their
avatars; VoiceOver announces the localized role.

### Physical iPhone first-use review

The 2026-10-31 iPhone 18 Pro review found hidden navigation labels, an extra
library step in Home’s cloning shortcut, disabled speech generation before model
setup, and dictation inviting a tap on a disabled microphone. Navigation now
keeps visible labels, Home opens clone creation directly, Speech pins model setup
before generation, and Dictate explains the required download. The same first-use
flows are implemented on Android. Licence acceptance remains explicit and closing
a review preserves the speech draft without starting a download.

Gallery opens the public [community catalogue](https://github.com/debpalash/omnivoice-gallery)
directly on the phone: named design recipes, recorded voices, packs and language
filters, with author and licence credits. A bundled snapshot and validated cache
keep browsing useful offline; opening Gallery refreshes its public manifest.
Recipes prefill native Voice Design. Recordings download only on Play or a
permission-confirmed import, with bounded HTTPS downloads and SHA-256 verification.
No model downloads or inference start while browsing. This phone keeps engine
presets; My computer keeps the paired library. Android implements the same flows.

Physical iPhone 18 Pro verification on 2026-10-31 confirms 50 named community
entries without pairing/models, Thalia playback, an explicit Cancel/permission
alert, and The Librarian's correct script and traits in Voice Design. With owner approval, the 166.9 MB Kitten selection was then downloaded and used
for real speech generation: a 9.0-second, 24 kHz mono WAV appeared in about seven
seconds including UI overhead, and playback ran on the phone without a paired
computer. Two presets produced different non-silent audio; pause, resume and
end-of-file replay passed, and the saved WAV retained its synthetic marker.
Community design/cloning inference still needs its separate model setup.

Speech exposes all seven phone speech engines in its Models picker. Preset engines
can generate without a saved voice; OmniVoice, Pocket TTS and ZipVoice require a
saved reference. Voice Design stays a separate explicit task. Selecting
a preset-only engine clears an incompatible reference. Downloads stay explicit
and licence-gated; no user test recordings ship with the model packs. ZipVoice's
vocoder, pronunciation data and lexicon are size/hash verified with its weights.
Voice Design remains limited to OmniVoice. The same choices ship on Android.

For the four new packs, explicitly prepare fixtures with
`python3 mobile/ios/scripts/fetch-smoke-models.py --accept-test-licences --speech-models`.
Run `realSpeechModelsProduceMarkedAudioOffline` with `VOICESTUDIO_REAL_SPEECH=1`,
`SHERPA_ONNX_LIBRARY` pointing to the pinned macOS runtime. The tests generate
their own Piper reference and decode it to 24 kHz for cloning. These fixture receipts never accept terms in the phone app.

OmniVoice Speech never falls back to bare voice design when a reference is
missing. That path produced non-speech for the short “Hi hello” repro on both the
phone and CPU. The phone pack now uses Q8_0 base / F32 codec (1.4 GB; 8 GB RAM).
Its legacy catalogue identifier remains stable; previous model files, voice data
and licence acceptance are preserved. The native regression runs actual saved-
voice generation through Whisper and rejects missing or repeated words, beyond
checking only PCM energy. Use `--omni-speech` with the fixture fetcher to prepare
the pack and `VOICESTUDIO_REAL_OMNI_SPEECH=1` with both native libraries to run
`realOmniSavedVoiceSpeechIsRecognizableWithoutRepeats`.
