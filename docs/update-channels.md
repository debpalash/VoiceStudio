# Desktop updates

VoiceStudio's desktop app follows **Stable** releases unless you opt in to
**Beta**. It checks for an
update about 10 seconds after launch and every six hours while it stays open;
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

Downloads show transferred size, total size, speed, percentage and ETA, and
can continue while synthesis, transcription or dubbing work runs. VoiceStudio
only enables **Restart to update** after the package has downloaded and passed
the updater's checksum verification, and blocks that restart until active work
has finished and current drafts are flushed.

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
package's SHA-512 checksum, and the app rejects a package that fails it. On
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
