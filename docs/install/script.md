# Shell installer (Electron)

The shell installer supports macOS (Apple Silicon and Intel) and Linux x64.
On Windows, download the `VoiceStudio-…-win-x64.exe` installer (or
`…-win-arm64.exe` on a Snapdragon/ARM PC) from
[Releases](https://github.com/debpalash/VoiceStudio/releases).

Or use PowerShell. Use the `.ps1` URL: the bare `/install` URL is not guaranteed to detect PowerShell and can return the shell script (#2973):

```powershell
irm https://voicestudio.sh/install.ps1 | iex
$env:VOICESTUDIO_VERSION='X.Y.Z'; irm https://voicestudio.sh/install.ps1 | iex
# Clear a previous version override before selecting main:
Remove-Item Env:VOICESTUDIO_VERSION -ErrorAction SilentlyContinue
$env:VOICESTUDIO_INSTALL_MODE='main'; irm https://voicestudio.sh/install.ps1 | iex
```

On ARM64 Windows the script installs the native ARM64 build and falls back to the x64 build when a release has none. Windows main builds also need Visual Studio C++ build tools (plus the ARM64 build tools on ARM). The script builds
an NSIS installer, opens its setup wizard, and waits for completion. Environment
overrides persist in the terminal; remove them to return to latest-release mode.

```sh
# Latest stable Electron release
curl -fsSL https://voicestudio.sh/install | sh

# A published Electron version, with or without a v prefix
curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z

# Clone main, build a desktop package, then install it
curl -fsSL https://voicestudio.sh/install | sh -s -- --main
```

`--source` is an alias for `--main`. It cannot be combined with `--version`.
Release packages are named `VoiceStudio-<version>-<os>-<arch>`; the installers
still find the `VoiceStudio-Electron-*` names that releases up to v0.5.7-beta.3 used.
After downloading the script for inspection, the equivalent local commands are
`sh scripts/install.sh`, `sh scripts/install.sh --version X.Y.Z`, and
`sh scripts/install.sh --main`.

## Release installation

If `voicestudio.sh/install` is unreachable or returns 404, run the same script
straight from the repository, or clone it and run `scripts/install.sh`:

```bash
curl -fsSL https://raw.githubusercontent.com/debpalash/VoiceStudio/main/scripts/install.sh | sh
```

Requires curl and `shasum` (included on macOS) or `sha256sum`. The installer
resolves the latest GitHub release tag, downloads the Electron DMG or AppImage,
and verifies its exact filename against that release's `SHA256SUMS.txt`.
Missing packages, missing checksums, and checksum mismatches stop installation.
The frozen `latest.json` Tauri updater feed is not used. Releases without an
Electron package cannot be installed through this script.

Quit VoiceStudio completely before replacing it (Command-Q on macOS). The shell
installer refuses to replace or uninstall a running app when `pgrep` is available,
and checks again before replacing files. macOS refreshes the installed app's
Launch Services registration so Launchpad can find the new version.
macOS installs `VoiceStudio.app` in
`/Applications`, or `~/Applications` if `/Applications` is not writable.
On macOS the installer also clears the `com.apple.quarantine` flag and, when
`codesign --verify --deep --strict` fails (releases shipped before the build
signed ad hoc, #2763), re-signs the bundle ad hoc so Gatekeeper does not report
"damaged". A valid or notarized signature is never touched. On an Intel Mac it
prints that the local backend needs Apple Silicon.
Linux installs the AppImage as `~/.local/bin/VoiceStudio`. Current builds expose
the package's unified `voicestudio` command through `~/.local/bin` on macOS and
Linux and configure PATH for Bash, Zsh, POSIX and Fish shells.
No separate Python installation is required. An existing custom launcher is
preserved: conflicting ownership stops installation before replacing the app;
see [Terminal command](#terminal-command). A command already
owned by the Python CLI package is retained. On a case-insensitive filesystem,
where `voicestudio` and `VoiceStudio` are the same file, the AppImage itself is the
command; a Python CLI command there stops installation instead of being
overwritten (choose another `VOICESTUDIO_INSTALL_DIR`).
Current Electron source builds also read archived setup configuration for custom
data/model folders and portable storage; explicit launch environment overrides
still win. The backend retains its existing durable Settings precedence. No files
are moved or copied. Previously published installers retain their shipped behavior.
The existing app is replaced only after staging succeeds. User settings,
projects, model downloads, and backend environments are not removed. Downgrades
may not read data created by newer versions; back up important projects first.

For isolated shell-installer testing, `VOICESTUDIO_INSTALL_DIR` selects an
installation directory; it must be an absolute, existing, writable directory. Invalid overrides fail without falling back elsewhere.

## Terminal command

`voicestudio` opens the desktop app. `voicestudio --help` shows the command tree;
`version`, `--version` and `-v` report the installed application's version offline,
from its bundle metadata.

Results print as readable text: lists as tables that fit the terminal, details as
aligned fields and indented sections. Piped to another program, tables become
complete tab-separated rows without a header (`voicestudio voices list | cut -f2`).
Add `--json` for the raw result as machine-readable output on stdout
(errors then go to stderr as JSON with a stable `code`). Exit codes: `0` success,
`1` failure, `2` invalid usage, `3` from `status` when VoiceStudio is not running.
`settings get` with no name shows every setting.

Tab completion covers commands, options and fixed values. Enable it once in your
shell's startup file:

| Shell | Add this line to |
|---|---|
| bash | `eval "$(voicestudio completion bash)"` in `~/.bashrc` |
| zsh | `eval "$(voicestudio completion zsh)"` in `~/.zshrc` |
| fish | `voicestudio completion fish \| source` in `~/.config/fish/config.fish` |
| PowerShell | `voicestudio completion powershell \| Out-String \| Invoke-Expression` in `$PROFILE` |

Some commands were renamed for consistency; the former names still work but are
no longer listed: `voices inspect` → `voices show-file`, `voices reference get` →
`voices reference download`, `dub transcript get` → `dub transcript show`,
`dub history-delete` → `dub history delete`, `settings hf-token status` →
`settings hf-token show`, `models install-status` / `install-cancel` → `models progress` /
`models cancel`, `engines install-status` → `engines progress`, `gallery save-voice` →
`gallery use`, `update check` → `update`, `update info` →
`version --verbose`. Audiobooks and stories now share `audiobook`: `audiobook jobs` →
`audiobook resumable`, `jobs longform list` / `delete` → `audiobook finished` /
`audiobook delete`, `stories render` → `audiobook render-story`, `stories resume` →
`audiobook resume`, and `stories encode` → `audio encode`.

Commands print readable text. Add `--json` for scripts: results go to stdout as
JSON and errors to stderr as `{"error": {"code": …, "message": …}}`, with stable
codes. `voicestudio status` exits 0 when VoiceStudio is running and 3 when it is
not (with `{"running": false}` under `--json`); usage errors exit 2 and other
failures 1. `voicestudio settings get` with no name shows every setting.

Every other command talks to the app's backend over HTTP. If VoiceStudio is not
running, a command that creates or changes something (`speak`, `voices list`,
`dub`, `settings set`, …) starts the app **in the background, without a window**,
waits for its backend and then runs; the first such command prints
`Starting VoiceStudio in the background…` on stderr. The app starts its backend
exactly as it does when you open it, so engines, settings and the port are the
same. While it runs in the background it shows its tray (menu bar) icon.

- Opening VoiceStudio from the menu, dock, tray or `voicestudio` shows a window in
  that same app; nothing restarts. Closing the window returns it to the background.
- After 10 minutes with no window, no requests and no running work (dubs, batches,
  downloads, installs, calls), the background app quits. Set
  `VOICESTUDIO_IDLE_SHUTDOWN_S` to change the period.
- `voicestudio stop` quits it now. It never closes a window you have open, and it
  does not stop a backend started some other way (a source checkout or Docker).
- `status`, connectivity tests and questions about live state (loaded models,
  running jobs, sharing, calls) report that VoiceStudio is stopped instead of
  starting it.
- If setup is not finished or the backend cannot start, the command says so and
  asks you to open VoiceStudio, instead of waiting.
- Background work (`dub upload`, `dub ingest`, `dub generate`) runs to the end:
  progress goes to stderr, a failed or cancelled task exits 1, and Ctrl+C also
  stops the task. `dub generate --output FILE` saves the dubbed audio track.
- `models install` and `models license accept` list every licence the model
  needs (OmniVoice also needs the Higgs audio tokenizer's) and accept them all
  only with `--accept-license`.

The app publishes its backend address in `backend.json` in its user-data folder,
so commands also find it on a fallback port. An explicit `--url`, `VOICESTUDIO_URL`
or saved connection URL selects an externally managed endpoint instead: those
commands never start the app. Credentials remain bound to their origin. When the
app itself is connected to a remote backend, commands ask you to select it with
`voicestudio connection set`.

Commands never install Python packages or models implicitly. `VOICESTUDIO_USER_DATA`
selects an Electron user-data directory and is passed to the app it starts;
`VOICESTUDIO_DESKTOP_APP` selects a nonstandard desktop location. On Linux,
starting the app in the background needs a graphical session; over SSH or on a
server, run a backend yourself and pass `--url`.

On macOS and Linux direct downloads, the app links `~/.local/bin/voicestudio` when
it starts. On the first launch, if new terminals would not find `~/.local/bin`,
it also adds a marked block (`# VoiceStudio terminal command`) to your shell
profiles, as Windows installers and Linux packages put the command on PATH
themselves. It does this once: if you remove the block, it stays removed, and
**Settings → General → Terminal command → Add to terminal** adds it back.

For older AppImages, the first metadata read extracts the application's manifest.
Subsequent reads use a local cache tied to the image's file identity, size and
timestamps; changing or replacing the image invalidates it.

To add the terminal command to an existing desktop installation from a checkout,
using Python 3.11+ and uv:

```bash
PYTHONPATH=cli python3 -m voicestudio_cli.install ./cli
```

In PowerShell:

```powershell
$env:PYTHONPATH = 'cli'
python -m voicestudio_cli.install ./cli
```

If the command already exists, installation stops. `--replace-launcher` explicitly
backs up a shell launcher or records a symlink target for desktop dispatch;
installation failure restores the previous command. Native executables are not
replaced. `--no-modify-path` disables shell configuration changes.

`version --verbose` reports how the app is installed and how it updates, offline.
`update` opens VoiceStudio at **Settings → Updates**, where the app's own updater
checks GitHub Releases on its saved channel and downloads, verifies and installs
an update, the same as from the app. The terminal command makes no update
request of its own, and otherwise contacts only the backend it is connected to;
choose the update channel in **Settings → Updates**. Installations managed by
a package manager (`VOICESTUDIO_DISABLE_UPDATER=1`) are updated by that package
manager. Store-managed installations remain owned by their store and
source installations require rebuilding. Installing the terminal command with uv
or pip does not make either tool responsible for updating the application.

## Uninstall

Quit VoiceStudio, then run:

```sh
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall
```

On macOS this removes the Electron app from `/Applications` and `~/Applications`;
on Linux it removes the script-installed `~/.local/bin/VoiceStudio` AppImage.
It also removes its own command symlink, leaving independently installed CLI
packages and custom launchers alone. Shared PATH entries remain available to
other tools.
`VOICESTUDIO_INSTALL_DIR` restricts removal to a custom installation directory.
On macOS the app is unregistered and moved into a uniquely named folder in Trash;
it is never kept as a hidden application under `/Applications`, where Launchpad
can still discover it. Linux uses a hidden sibling recovery directory. The exact
recovery path is printed. Restore it by moving the app back, or delete that copy to
reclaim disk space. Package-manager installations must be removed through their
package manager. Archived Tauri apps are not removed.

```powershell
Remove-Item Env:VOICESTUDIO_VERSION -ErrorAction SilentlyContinue
$env:VOICESTUDIO_INSTALL_MODE='uninstall'; irm https://voicestudio.sh/install.ps1 | iex
```

Windows skips archived Tauri registry entries and opens one verified Electron uninstall wizard.
Use `-Silent` for unattended Windows install/uninstall (for example, CI); interactive setup remains the default. All platforms preserve
settings, projects, backend environments and models. To delete app data, use
VoiceStudio's in-app data-removal confirmation **before** uninstalling; the CLI
does not guess custom storage locations or delete shared model caches.

## Building main

Install Git, Node.js 22 or newer, Bun (the version pinned in root
`package.json`), stable Rust/Cargo, and uv before running `--main`. Packaging
uses uv to build the bundled CLI with a pinned PyInstaller tool and Python 3.11.
On macOS, install Xcode Command Line Tools (`xcode-select --install`); use a
native arm64 terminal on Apple Silicon. On Debian/Ubuntu, install the native
dependencies used by the Electron build:

```sh
sudo apt-get install build-essential pkg-config libasound2-dev libxdo-dev \
  libxtst-dev libx11-dev libxkbcommon-dev libwayland-dev libssl-dev \
  binutils zsync
```

Other Linux distributions need equivalent development packages. A graphical
session and the normal Electron runtime libraries are required to run the app.
The build downloads dependencies and may take several minutes and multiple GB.

Each invocation clones `main` into a temporary directory, prints its commit,
installs locked Bun dependencies, packages Electron and its Rust native helper,
and installs the result. It never updates or resets an existing source checkout.
Temporary files are removed on success or failure. Local packages are unsigned;
normal OS security prompts may apply. No publishing occurs.

Open the installed app to configure its local Python backend and choose models.
The script does not start a development server or download model weights.
Intel Macs can use the Electron UI with a remote backend; local Python/ML
support remains subject to the engine's platform requirements.

## Installer regression tests

From `electron/`, run `node --test tests/shell-installer.test.mjs`. These tests
mock downloads and builds in temporary directories; they do not install an app
or download models. A real packaged build and first-run smoke test are separate
checks in the Electron release workflow.

## Deployment

`deploy/install-worker/worker.mjs` bundles the tested shell and PowerShell
scripts rather than fetching mutable `main` content at request time. Deploy
with `bunx wrangler deploy --config deploy/install-worker/wrangler.jsonc` after
testing. Both `voicestudio.sh/install*` routes are retained. Existing Worker
secrets are preserved. A deployment is needed for script updates to go live.

Worker routing tests: `node --test tests/installer-worker.test.mjs` from
`electron/`. PowerShell flow tests: `pwsh -NoProfile -File
tests/powershell-installer.ps1`. They mock Windows setup execution; a real
Windows install/build still needs a Windows machine.
