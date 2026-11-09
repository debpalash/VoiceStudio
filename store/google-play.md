# Google Play runbook

Status: listing prepared; no Play Console app exists yet.

Package name: `sh.voicestudio.mobile`

Everything the Play Console asks for is in [`google-play/listing.json`](google-play/listing.json),
so the answers stay reviewable in Git. Graphics are rendered from the app's own
brand sources by `uv run python scripts/create_google_play_assets.py`.
`tests/test_google_play_listing.py` checks Play's length and size limits and that
every permission in the Android manifest has a declaration here.

## Owner steps

1. Register a Play developer account as an **organization** (Yupcha Softwares
   Private Limited; needs a D-U-N-S number). New personal accounts must run a
   closed test with 12 testers for 14 days before production.
2. Create the app with the package name above, keep Play App Signing on, and set
   the contact email.
3. **Main store listing:** title, short and full description (join the
   `fullDescription` lines with newlines), icon, feature graphic, category and
   2–8 phone screenshots.
4. **App content:** privacy policy URL, ads (none), app access (no login), target
   audience, content rating questionnaire and the data safety form from
   `dataSafety` (optional content reports are collected; no data is shared).
5. **Permissions and foreground services:** use the reasons in `permissions`.
   Play asks for a short video of each foreground service type in use.
6. Prepare a signed APK/AAB without a store upload using **mobile-release**
   from `main`: leave `release_ref` empty, enable `android`, and disable `ios`
   and `signed_ios`. Its `mobile-release` environment needs the four Android
   upload-key secrets. Download `VoiceStudio-android-release` and upload
   `VoiceStudio.aab` to internal testing by hand, then use **Android Play internal
   testing** with the same upload key in the `google-play` environment
   ([`docs/mobile.md`](../docs/mobile.md#play-internal-testing-maintainers)).

## Before production

- **Report endpoint live:** the in-app Report action posts to
  `appapi.voicestudio.sh/v1/content-reports`; it must be deployed before the
  first Play review, or reviewers' test reports fail.
- **Privacy policy:** `https://voicestudio.sh/privacy` should describe the phone
  apps: what leaves the phone (model downloads, reports the user sends and
  requests to the paired computer) and that reports are kept 180 days.
- **Screenshots:** the `android-screenshots` job in the `mobile` workflow
  captures Home, Speak (with a playable take made on the phone), Voices (with
  one phone voice), Dub (with two target languages), Dictate, Models and Settings from the debug app in English on a 1080×1920 emulator,
  phone-only with the smallest speech model. It runs on pushes to `main` that
  touch the mobile app, or via Actions → `mobile` → Run workflow. Download the
  `android-play-screenshots` artifact (kept 30 days) from that run's summary page.
