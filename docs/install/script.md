# Shell installer (Electron)

The shell installer supports macOS (Apple Silicon and Intel) and Linux x64.
On Windows, download the `VoiceStudio-Electron-…-win-x64.exe` installer from
[Releases](https://github.com/debpalash/VoiceStudio/releases).

Or use PowerShell (the endpoint detects PowerShell's user agent):

```powershell
irm https://voicestudio.sh/install | iex
$env:VOICESTUDIO_VERSION='X.Y.Z'; irm https://voicestudio.sh/install | iex
# Clear a previous version override before selecting main:
Remove-Item Env:VOICESTUDIO_VERSION -ErrorAction SilentlyContinue
$env:VOICESTUDIO_INSTALL_MODE='main'; irm https://voicestudio.sh/install | iex
```

Windows main builds also need Visual Studio C++ build tools. The script builds
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
After downloading the script for inspection, the equivalent local commands are
`sh scripts/install.sh`, `sh scripts/install.sh --version X.Y.Z`, and
`sh scripts/install.sh --main`.

## Release installation

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
Linux installs `~/.local/bin/VoiceStudio`; include `~/.local/bin` in your PATH.
Current Electron source builds also read archived setup configuration for custom
data/model folders and portable storage; explicit launch environment overrides
still win. The backend retains its existing durable Settings precedence. No files
are moved or copied. Previously published installers retain their shipped behavior.
The existing app is replaced only after staging succeeds. User settings,
projects, model downloads, and backend environments are not removed. Downgrades
may not read data created by newer versions; back up important projects first.

For isolated shell-installer testing, `VOICESTUDIO_INSTALL_DIR` selects an
installation directory; it must be an absolute, existing, writable directory. Invalid overrides fail without falling back elsewhere.

## Uninstall

Quit VoiceStudio, then run:

```sh
curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall
```

On macOS this removes the Electron app from `/Applications` and `~/Applications`;
on Linux it removes the script-installed `~/.local/bin/VoiceStudio` AppImage.
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
`package.json`), and stable Rust/Cargo before running `--main`.
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
