# Desktop updates

VoiceStudio's desktop app offers **stable releases only**. It checks for an
update about 10 seconds after launch and every six hours while it stays open;
**Settings → Updates** also has a manual check. The app never downloads an
update until you ask, and an available update is only offered, never forced.

| Build | Where it comes from | Who it's for |
|-------|--------------------|--------------|
| **Stable** | The latest published `vX.Y.Z` GitHub Release. | Everyone. This is the only update channel. |

There is no Preview update channel and no preview desktop feed. To try changes
before they are released:

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

There are **no accounts or updater telemetry**. A check fetches one small
platform/architecture-specific manifest from
`github.com/debpalash/VoiceStudio/releases/latest/download/`; it carries the
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
   A snapshot interrupted mid-copy leaves a `.part-<pid>` file that is never
   treated as a backup and is removed on the next snapshot.
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
