#!/bin/sh
# Install Electron from a release, or build and install current main.
set -eu

die() { printf 'VoiceStudio: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
usage() {
    cat <<'HELP'
VoiceStudio Electron installer (macOS / Linux)

  curl -fsSL https://voicestudio.sh/install | sh
  curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z
  curl -fsSL https://voicestudio.sh/install | sh -s -- --main

  --version VERSION  Install a published Electron version (optional v prefix)
  --main, --source   Clone main, build Electron, and install the desktop app
  --binary          Install a release (default)
  --uninstall       Remove the installed app, preserving all user data
  --help            Show this help

Release installs require curl and a SHA-256 tool. Main builds also require
Git, Node.js 22+, Bun, Rust/Cargo, and native build dependencies (see README).
The installer preserves settings, models, and projects. Quit VoiceStudio first.
Versions without Electron artifacts are not supported; Tauri is archived.
HELP
}

MODE=binary
VERSION=
UNINSTALL=0
INSTALL_OPTION=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --main|--source) MODE=main; INSTALL_OPTION=1 ;;
        --binary) MODE=binary; INSTALL_OPTION=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --version)
            [ "$#" -ge 2 ] || die '--version requires a value'
            VERSION=${2#v}; INSTALL_OPTION=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac
    shift
done
[ "$UNINSTALL-$INSTALL_OPTION" != 1-1 ] || die '--uninstall cannot be combined with installation options'
[ "$MODE" != main ] || [ -z "$VERSION" ] || die '--main cannot be combined with --version'
valid_version() {
    # Versions are used in URLs and filenames; reject paths and shell syntax.
    printf '%s\n' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+([.-][A-Za-z0-9]+)*)?$'
}
[ -z "$VERSION" ] || valid_version "$VERSION" || die 'Invalid version; use X.Y.Z or X.Y.Z-prerelease'
case "$(uname -s)" in
    Darwin) OS=mac ;;
    Linux) OS=linux ;;
    *) die 'This shell installer supports macOS and Linux. On Windows, use the Electron .exe from GitHub Releases.' ;;
esac
if [ "$OS" = mac ]; then
    PATH="$PATH:/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support"
    export PATH
fi
if [ -n "${VOICESTUDIO_INSTALL_DIR:-}" ]; then
    case "$VOICESTUDIO_INSTALL_DIR" in /*) ;; *) die 'Install directory must be absolute';; esac
    [ -d "$VOICESTUDIO_INSTALL_DIR" ] && [ -w "$VOICESTUDIO_INSTALL_DIR" ] || die 'Custom install directory must exist and be writable.'
fi
require_closed() {
    if have pgrep && { pgrep -f '/VoiceStudio[.]app/Contents/MacOS/VoiceStudio([[:space:]]|$)' >/dev/null 2>&1 || pgrep -x VoiceStudio >/dev/null 2>&1 || pgrep -f '(^|/)voicestudio-electron([[:space:]]|$)' >/dev/null 2>&1; }; then
        die 'Quit VoiceStudio completely (macOS: Command-Q), then run this command again. No app files were changed.'
    fi
}
require_closed
if [ "$UNINSTALL" = 1 ]; then
    # Move only the named application into a recoverable sibling directory.
    # Never traverse data locations or delete shared model caches.
    remove_app() {
        target=$1
        [ -e "$target" ] || [ -L "$target" ] || return 0
        [ ! -L "$target" ] || die "Refusing symlink: $target"
        if [ "$OS" = mac ]; then
            [ -f "$target/Contents/Resources/app.asar" ] || die "Not an Electron app: $target"
        else
            [ -f "$target" ] || die "Not an AppImage file: $target"
        fi
        if [ "$OS" = mac ]; then
            # Hidden .app backups inside Applications still appear in Launchpad.
            # Trash keeps recovery possible without leaving a launchable duplicate.
            mkdir -p "$HOME/.Trash"
            recovery=$(mktemp -d "$HOME/.Trash/VoiceStudio-uninstalled.XXXXXXXX")
            # lsregister may return -10814 even after removing a stale record.
            # Moving to Trash is still required to prevent rediscovery.
            lsregister -u "$target" || printf 'Warning: macOS could not refresh the old registration; moving the app to Trash.\n' >&2
        else
            recovery=$(mktemp -d "$(dirname "$target")/.voicestudio-uninstalled.XXXXXXXX")
        fi
        mv "$target" "$recovery/" || die "Could not remove $target; check permissions."
        printf 'Uninstalled: %s\nRecoverable copy: %s\n' "$target" "$recovery"
    }
    printf 'Quit VoiceStudio before uninstalling.\n'
    if [ -n "${VOICESTUDIO_INSTALL_DIR:-}" ]; then
        case "$VOICESTUDIO_INSTALL_DIR" in /*) ;; *) die 'Install directory must be absolute';; esac
        if [ "$OS" = mac ]; then remove_app "$VOICESTUDIO_INSTALL_DIR/VoiceStudio.app";
        else remove_app "$VOICESTUDIO_INSTALL_DIR/VoiceStudio"; fi
    elif [ "$OS" = mac ]; then
        remove_app /Applications/VoiceStudio.app
        remove_app "$HOME/Applications/VoiceStudio.app"
    else
        remove_app "$HOME/.local/bin/VoiceStudio"
    fi
    printf 'Uninstall complete. Settings, models and projects are preserved.\n'
    exit 0
fi
have curl || die 'Install curl, then retry.'
case "$(uname -m)" in
    arm64|aarch64) ARCH=arm64 ;;
    x86_64|amd64) ARCH=x64 ;;
    *) die 'Unsupported processor architecture.' ;;
esac
if [ "$OS" = mac ] && [ "$ARCH" = x64 ] && [ "$(sysctl -in hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
    ARCH=arm64
    [ "$MODE" != main ] || die 'Run --main from a native Apple Silicon terminal, not Rosetta.'
fi
[ "$OS-$ARCH" != linux-arm64 ] || die 'Linux arm64 Electron packages are not supported yet.'

WORK=$(mktemp -d "${TMPDIR:-/tmp}/voicestudio-install.XXXXXXXX")
MOUNT=
STAGED=
BACKUP=
DEST=
cleanup() {
    if [ -n "$MOUNT" ]; then hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true; fi
    if [ -n "$BACKUP" ] && [ -e "$BACKUP" ] && [ ! -e "$DEST" ]; then mv "$BACKUP" "$DEST"; fi
    if [ -n "$STAGED" ] && [ -d "$STAGED" ]; then rm -rf "$STAGED"; fi
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
download() { curl --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 1800 -fLsS --retry 3 "$1" -o "$2"; }

install_app() {
    require_closed
    if [ "$OS" = mac ]; then
        DEST_DIR=${VOICESTUDIO_INSTALL_DIR:-/Applications}
        if [ ! -w "$DEST_DIR" ]; then DEST_DIR="$HOME/Applications"; mkdir -p "$DEST_DIR"; fi
        DEST="$DEST_DIR/VoiceStudio.app"
        STAGED=$(mktemp -d "$DEST_DIR/.voicestudio-install.XXXXXXXX")
        # Copy and validate first, retaining the prior app until the copy succeeds.
        ditto "$1" "$STAGED/VoiceStudio.app"
        [ -f "$STAGED/VoiceStudio.app/Contents/Info.plist" ] || die 'Invalid application bundle.'
        if [ -e "$DEST" ]; then
            BACKUP="$STAGED/previous.app"
            mv "$DEST" "$BACKUP"
        fi
        mv "$STAGED/VoiceStudio.app" "$DEST"
        lsregister -f "$DEST" || printf 'Warning: macOS registration failed; open the installed app directly.\n' >&2
    else
        DEST_DIR=${VOICESTUDIO_INSTALL_DIR:-"$HOME/.local/bin"}
        mkdir -p "$DEST_DIR"
        DEST="$DEST_DIR/VoiceStudio"
        STAGED=$(mktemp -d "$DEST_DIR/.voicestudio-install.XXXXXXXX")
        cp "$1" "$STAGED/VoiceStudio"
        chmod +x "$STAGED/VoiceStudio"
        mv -f "$STAGED/VoiceStudio" "$DEST"
    fi
    printf 'Installed Electron: %s\n' "$DEST"
    printf 'Open VoiceStudio to configure its local backend and choose models. Existing data is preserved.\n'
    if [ "$OS" = mac ]; then printf 'Launch the installed app: open "%s"\n' "$DEST"; fi
}

if [ "$MODE" = main ]; then
    for tool in git node bun cargo; do have "$tool" || die "Main builds require $tool; see the source-build prerequisites in README."; done
    if [ "$OS" = linux ]; then
        for tool in readelf zsyncmake; do have "$tool" || die "Linux main builds require $tool; install binutils and zsync (see docs/install/script.md)."; done
    fi
    node -e 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)' || die 'Node.js 22 or newer is required.'
    printf 'Building and installing Electron from main (this may take several minutes).\n'
    # Never pull into, reset, or build from an existing user checkout.
    git clone --depth 1 --branch main --single-branch https://github.com/debpalash/VoiceStudio.git "$WORK/source"
    (
        cd "$WORK/source"
        printf 'Source commit: '; git rev-parse HEAD
        bun install --frozen-lockfile
        # Packaging includes the backend and builds its Rust native helper.
        # Backend setup is performed by the installed app on first launch.
        export CSC_IDENTITY_AUTO_DISCOVERY=false
        cd electron
        bun run build
        node tests/packaging-contract.mjs
        # Invoke the builder directly: bun appends flags to the last command
        # in a chained package script, not necessarily to electron-builder.
        bun run electron-builder --config electron-builder.config.mjs --publish never --"$OS" --"$ARCH"
        if [ "$OS" = linux ]; then node scripts/embed-appimage-update.mjs; fi
        node tests/update-package-contract.mjs
    )
    VERSION=$(node -p 'require(process.argv[1]).version' "$WORK/source/frontend/package.json")
    valid_version "$VERSION" || die 'Invalid version in source checkout.'
    PACKAGE_DIR="$WORK/source/electron/release"
else
    RELEASES=https://github.com/debpalash/VoiceStudio/releases
    if [ -z "$VERSION" ]; then
        # latest.json is the frozen Tauri feed. Resolve the release tag instead.
        URL=$(curl --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 60 -fLsS --retry 3 -o /dev/null -w '%{url_effective}' "$RELEASES/latest")
        case "$URL" in "$RELEASES/tag/v"*) VERSION=${URL##*/v} ;; *) die 'Could not resolve the latest release tag.' ;; esac
        valid_version "$VERSION" || die 'Invalid latest release version.'
    fi
    PACKAGE_DIR="$WORK"
fi
case "$OS" in
    mac) ASSET="VoiceStudio-Electron-$VERSION-mac-$ARCH.dmg" ;;
    linux) ASSET="VoiceStudio-Electron-$VERSION-linux-x64.AppImage" ;;
esac
PACKAGE="$PACKAGE_DIR/$ASSET"
if [ "$MODE" = binary ]; then
    BASE="$RELEASES/download/v$VERSION"
    printf 'Downloading %s\n' "$ASSET"
    download "$BASE/$ASSET" "$PACKAGE" || die "No downloadable Electron package for v$VERSION ($OS/$ARCH). Check the release; legacy Tauri versions are not installed."
    download "$BASE/SHA256SUMS.txt" "$WORK/SHA256SUMS.txt" || die 'Published checksums are unavailable; refusing to install.'
    EXPECTED=$(awk -v asset="$ASSET" '$2 == asset {print $1}' "$WORK/SHA256SUMS.txt")
    printf '%s\n' "$EXPECTED" | grep -Eq '^[a-fA-F0-9]{64}$' || die 'Missing, duplicate, or invalid checksum entry.'
    if have shasum; then ACTUAL=$(shasum -a 256 "$PACKAGE" | awk '{print $1}');
    elif have sha256sum; then ACTUAL=$(sha256sum "$PACKAGE" | awk '{print $1}');
    else die 'A SHA-256 tool (shasum or sha256sum) is required; refusing to install.'; fi
    [ "$(printf '%s' "$EXPECTED" | tr 'A-F' 'a-f')" = "$ACTUAL" ] || die 'Checksum mismatch; refusing to install.'
    printf 'Checksum verified.\n'
fi
[ -f "$PACKAGE" ] || die "Expected Electron package not found: $PACKAGE"
if [ "$OS" = mac ]; then
    MOUNT="$WORK/mount"
    mkdir "$MOUNT"
    hdiutil attach -readonly -nobrowse -mountpoint "$MOUNT" "$PACKAGE"
    [ -d "$MOUNT/VoiceStudio.app" ] || die 'VoiceStudio.app is missing from the disk image.'
    install_app "$MOUNT/VoiceStudio.app"
else
    install_app "$PACKAGE"
fi
