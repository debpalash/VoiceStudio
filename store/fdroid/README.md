# VoiceStudio Android distribution

The publisher-operated repository for F-Droid clients is
`https://debpalash.github.io/VoiceStudio/fdroid/repo`. It distributes the unchanged,
release-signed APK from the latest stable GitHub Release. This is separate from
inclusion in F-Droid.org's main catalogue.

Add this URL in an F-Droid-compatible client's repository settings and verify
the certificate SHA-256 fingerprint:

```text
B0EEBDF9745A00FAFF4EDAFBF63E2B20521117C5B368BD30752AFCA9FC50B798
```

The APK uses the developer's existing release certificate, not the repository
index key. Play-delivered builds can have a different signing certificate.
Preserve projects before changing installation channels; do not promise an
in-place update between differently signed installations.

## Obtainium

Add `https://github.com/debpalash/VoiceStudio` in Obtainium, or import
[`../obtainium.json`](../obtainium.json). The repository's landing page also has
an import link. Obtainium follows published APK releases, not CI artifacts or
unreleased commits on `main`. Prereleases are an explicit client choice.

## Publishing

[`android-repository.yml`](../../.github/workflows/android-repository.yml) tests
the publisher on pull requests using a fresh, temporary index key. Production
deployments run after relevant merges to `main`, stable release publication, or
a manual dispatch from `main`. They rebuild the indexes with the persistent key
in the `github-pages` environment and publish only the generated public site.

The environment needs `FDROID_REPO_KEYSTORE_B64` (PKCS12, alias `voicestudio`)
and `FDROID_REPO_PASSWORD`. Keep an offline backup of that key and password.
Losing or rotating the index key requires users to trust a new repository.
Private keys and credentials are never checked in or copied into Pages artifacts.

`repository.json` pins both signing identities. The publisher checks the release
origin, stable tag, API digest, APK bytes, package/version and both manifest flags
before invoking `fdroid update`. It verifies the resulting APK hash and both
index signatures/certificates before publishing. A signing change fails closed.

Local preparation uses an isolated environment:

```sh
uv venv /tmp/voicestudio-fdroid-tools --python 3.11
uv pip sync --require-hashes --python /tmp/voicestudio-fdroid-tools/bin/python store/fdroid/requirements.txt
/tmp/voicestudio-fdroid-tools/bin/python tests/test_android_repository.py
```

Use the workflow for production signing. Do not publish a pull request's preview
artifact: its temporary certificate intentionally differs from the public pin.

## Main F-Droid catalogue and IzzyOnDroid

Neither upstream catalogue inclusion is claimed by this repository.

- F-Droid's [inclusion policy](https://f-droid.org/docs/Inclusion_Policy/) requires
  a compliant source build and dependency review. Our current Android build
  downloads the pinned Sherpa-ONNX AAR from GitHub (`mobile/native/pins.json`,
  `mobile/native/android/fetch.py`); that needs a source-build recipe or an
  accepted dependency arrangement before submission. The native notices record
  17 components and their corresponding-source references. The catalogue also
  offers optional model weights with restrictive or unassessed licences, so the
  independent repository declares `NonFreeAssets` and explains the separate terms.
- IzzyOnDroid's [inclusion policy](https://izzyondroid.org/docs/general/AppInclusionPolicy/)
  usually limits APKs to 30 MB and disallows AI-generated application code.
  The released APK is about 130 MB. Neither problem is solved by adding metadata;
  no inclusion request claiming eligibility is submitted.

The Fastlane listing in `fastlane/metadata/android/en-US` can be reused for a
future eligible catalogue submission. Its two screenshots are real Android
emulator captures from the 2026-10-31 store asset preparation; they are not a new
physical-device test or proof of changes made after that capture.

These channels do not require Play Console's foreground-service declaration
videos. Android's foreground-service permissions and execution rules still apply.
