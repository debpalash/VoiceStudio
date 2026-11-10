# Desktop updates

VoiceStudio's desktop app follows **Stable** releases unless you opt in to
**Beta**. It checks for an
update about 10 seconds after launch and every six hours while it stays open.
A check that fails is retried after one hour, then two, then four, and back to
every six hours; the first successful check restores the six-hour rhythm.
**Settings → Updates** also has a manual check. The app never downloads an
update until you ask, and an available update is only offered, never forced.

| Build | Where it comes from | Who it's for |
|-------|--------------------|--------------|
| **Stable** | The latest published `vX.Y.Z` GitHub Release. | Everyone. This is the default. |
| **Beta** | The newest `vX.Y.Z-beta.N` prerelease, cut from `main` before the matching stable release. | Testers who want fixes early and can live with bugs. Opt in under **Settings → Updates → Update channel**. |

Switching channel takes effect immediately and is remembered across restarts.
It is locked while an update is downloading or waiting to restart.

- **Beta rolls forward onto Stable.** When a stable release passes your beta
  (for example `0.5.9` after `0.5.9-beta.3`), Beta installs it, so you never
  linger on an old beta.
- **Switching back to Stable never downgrades.** If your beta is newer than the
  latest stable, you stay on it until the next stable release.
- Beta builds are GitHub *prereleases*: they never become the `latest` release
  and Stable users never see them. Microsoft Store, Snap Store and Mac App
  Store builds are Stable-only.
- Beta Linux AppImages update through the in-app updater, not AppImageUpdate.

There is no separate preview desktop feed. To try changes between betas:

- **Run `main` from source** — see the platform install guides
  ([macOS](install/macos.md#building-from-source) ·
  [Windows](install/windows.md#building-from-source) ·
  [Linux](install/linux.md#building-from-source)), or build and install a
  package from `main` with the [install script's `--main` mode](install/script.md#building-main).
- **Docker** — `ghcr.io/debpalash/voicestudio:latest` follows `main`; see
  [image tags](install/docker.md#image-tags).

When a check finds an update, the installer downloads and is verified in the
background (**Settings → Updates → Download updates automatically**, on by
default; turn it off to download only when you click **Download update**).
A background download that fails on the network stays quiet and is retried at
the next check. While an update downloads, is ready, or has failed, a small
pill sits in the title bar left of your account picture: a progress ring,
**Restart**, or **Retry**. Click it for the version change, progress (size,
speed, percentage and ETA), the release notes, and a link to the GitHub
release.

Downloads continue while synthesis, transcription or dubbing work runs. A
download that receives no data for 45 seconds is cancelled and retried once;
if it still fails, **Retry** starts it again and **Download manually** opens
the version's GitHub release page. VoiceStudio only offers **Restart** after
the package has downloaded and passed the updater's checksum verification.
Restarting while work runs asks first ("Restarting will stop running
tasks"); drafts are flushed before the restart.

A verified update also installs when you quit VoiceStudio on macOS, Windows
and the Linux AppImage, so **Later** just hides the prompt for that version.
A `.deb` install does not install on quit, because installing needs an
administrator prompt: use **Restart**, or install the newer `.deb`. If an
update did not take effect, the next launch says so and offers **Retry** and
**Download manually**. Errors name the step that failed: checking, download,
verification or install.

Updates download only the parts of the installer that changed when they can,
and the full installer otherwise; the app picks automatically and checks the
result the same way either way. The size shown is the full installer for your
platform; a patch download finishes early.

| Install | Patch updates |
|---|---|
| Windows | Yes, from the first update |
| macOS | From the second update: the first one after installing from the DMG downloads in full |
| Linux AppImage | Yes |
| Linux `.deb` | No: install the newer `.deb` |

There are **no accounts or updater telemetry**. A check fetches one small
platform/architecture-specific manifest from
`github.com/debpalash/VoiceStudio/releases/latest/download/` (Stable), or, on
Beta, from the newest beta or stable release found through the public GitHub
releases list; it carries the
package's SHA-512 checksum, and the app rejects a package that fails it.
When GitHub's release-download host keeps failing (5xx errors, rate limiting
or timeouts) the app retries that check or download through GitHub's public
API instead: one request to `api.github.com` finds the release (Beta reuses
the lookup it already made), and the manifest and installer come from the
same release assets through `api.github.com/repos/debpalash/VoiceStudio/releases/assets/<id>`.
Those downloads skip patch updates, because GitHub allows only 60 anonymous
API requests an hour, and pass the same checksum verification. The next
check goes back to the normal host. On
Linux the in-app updater uses the AppImage; update a `.deb` installation by
installing the newer `.deb`. Source checkouts do not self-update.

The archived Tauri app keeps its frozen, signed `latest.json` feeds at
immutable URLs; they never offer Electron installers. Move to Electron with the
[migration guide](electron-migration.md).

## Your data during updates

Your voices, projects, history, and settings live in a SQLite database
(`omnivoice.db`) outside the app bundle, so replacing the app never touches
them. On the **first launch of an updated build**, if the new version needs a
database schema upgrade, VoiceStudio:

1. **Backs up the database first** — a consistent snapshot is written next to
   it as `omnivoice.db.backup-<version>-<n>` before any migration runs. The
   newest **3** backups are kept; older ones are pruned automatically.
   An interrupted copy leaves a `.part-<pid>` temporary file. Partial files are
   never shown as backups, and the next snapshot removes abandoned ones.
   (Databases over 500 MB skip the snapshot, with a log line saying so.)
2. **Stops instead of guessing** — if a migration fails midway, the app does
   *not* start on a half-migrated database and does *not* silently restore
   anything. It shows an error naming the backup path so you (or a support
   thread) decide: retry, report the issue, or roll back by replacing
   `omnivoice.db` with the backup.

**Settings → Updates** shows the timestamp of the latest backup, the release
notes of any available update, and a **What's new** reader for the shipped
changelog — all local, no extra network calls.

The Python environment (`.venv`) is also updated non-destructively: dependency
drift after an app update is reconciled **in place** with `uv sync`, and a
failed sync keeps the previous environment working. The venv is only ever
rebuilt when its interpreter is *confirmed* broken (structural check + a
direct probe) or when you explicitly use **Clean & Retry**.
