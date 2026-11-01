# Request for packaging: VoiceStudio

This is a packaging-assistance request, not a claim that the current build has
passed F-Droid's source-build checks. The publisher authorizes inclusion.

- Application ID: `sh.voicestudio.mobile`
- Source: https://github.com/debpalash/VoiceStudio
- Licence: AGPL-3.0-only
- Android source: `mobile/android/`
- Latest published version at preparation: `v0.5.7`, version code `3586502`
- Developer APK certificate SHA-256:
  `A4CDFD177375F4B8F9448996C34E246C73F1FFA97447A47B842051E27EA74730`
- Metadata, icon and real Android screenshots:
  `fastlane/metadata/android/en-US/` (introduced in upstream PR #3765)
- Issue tracker/contact: https://github.com/debpalash/VoiceStudio/issues
- Privacy policy: https://voicestudio.sh/privacy

VoiceStudio runs speech synthesis, voice cloning with consent, transcription
and dubbing on the phone after explicit model downloads. It can also pair with
the user's own VoiceStudio computer. No account is required for local use.
Models and results work offline after downloads. A user can explicitly send a
content report to the developer; phone analytics does not transmit events in
the current implementation.

## Source packaging work to resolve

The normal build runs `mobile/native/android/fetch.py` and `build.sh` through
Gradle `preBuild`. It downloads a hash-pinned Sherpa-ONNX AAR from GitHub, plus
pinned OmniVoice/llama.cpp/ggml source archives. The AAR is the principal native
dependency packaging obstacle; its public corresponding source is available,
but a GitHub binary download is not an established F-Droid dependency route.

Pins and SHA-256 values are in `mobile/native/pins.json` and
`mobile/native/android/source-checksums.json`. The app bundles notices and
corresponding-source references for 17 native components in
`mobile/android/app/src/main/assets/licenses/native-sources.json`.

A relevant accepted pattern is [Ramblr's F-Droid source-build work](https://gitlab.com/fdroid/fdroiddata/-/merge_requests/42401):
source-build the pinned Sherpa-ONNX code and its native source dependencies,
use an accepted Maven ONNX Runtime dependency, and provide all CMake
FetchContent sources before entering the network-isolated build. This has not
yet been implemented or verified for VoiceStudio's pinned runtime versions.
The VoiceStudio native libraries must preserve the full current model/API
coverage and the two isolated GGML engines; removing engines to make a build
pass is not intended.

The build uses JDK 21, Android SDK 36, NDK `27.1.12297006` and CMake `3.22.1`.
The canonical version comes from the root `package.json`; the maintained
release workflow supplies a monotonically increasing
`VOICESTUDIO_ANDROID_VERSION_CODE`. A catalogue recipe must set a matching
version code rather than assume the local fallback code matches the published
APK. Developer-signature reproduction has not been established.

## Model and network declarations

Optional model weights have separate terms, including non-commercial and
unassessed licences. They are not bundled and download only after the user
chooses them. `NonFreeAssets` should be disclosed. The catalogue and licence
records are in `mobile/contract/models.json`; please also assess whether the
Hugging Face downloads warrant `NonFreeNet`, as in the existing SherpaTTS entry.

The publisher-operated binary repository uses the developer's signed release
APK and is independently available to F-Droid clients. This request is for the
separate F-Droid.org source-built catalogue; its acceptance is not implied by
that repository or by the presence of Fastlane metadata.
