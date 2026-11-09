# VoiceStudio — Running and Developing with Nix / NixOS

The repository includes a community-maintained Nix flake (`flake.nix`) with development shells for Linux and Apple Silicon, an FHS environment for NixOS, and a Nix-built Linux x64 app. The non-blocking `Nix flake` CI workflow (`.github/workflows/nix.yml`) evaluates it and builds the Linux app on every change that can reach the build (docs-, test- and mobile-only changes are skipped); the release installers remain the supported install path.

## Prerequisites

- [Nix](https://nixos.org/download.html) with [flakes enabled](https://nixos.wiki/wiki/Flakes#Enable_flakes).

## Getting Started

`bun run dev` installs the JavaScript dependencies and creates the Python environment itself (it skips the Python setup when `OMNIVOICE_BACKEND_CMD` or `VOICESTUDIO_SKIP_BACKEND=1` selects an external backend), so each shell below needs only that command.

### 1. FHS environment (recommended on NixOS)

PyTorch, WhisperX and the other wheels `uv` installs expect standard Linux paths such as `/lib64` and `/usr/lib`. The FHS environment (Linux only) provides them and lets `uv` use its own managed CPython:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio

nix develop .#fhs   # or: nix run .#fhs
bun run dev
```

### 2. Development shell

```bash
nix develop
bun run dev
```

On Linux the shell provides:
- **Toolchains**: `bun`, `nodejs_22`, `uv`, `python311`, `rustc`, `cargo`, `clang`, `cmake`, `pkg-config`.
- **Libraries**: `alsa-lib`, `libxdo` (built from xdotool's sources, because nixpkgs' `xdotool` ships no shared `libxdo`), `libXtst`, `libx11`, `libxkbcommon`, `wayland`, `openssl`, `ffmpeg`, plus the Electron runtime libraries.
- nixpkgs' patched Electron 44 for `bun run dev` (`ELECTRON_OVERRIDE_DIST_PATH`), since the binary the npm package downloads cannot start on NixOS.
- `UV_PYTHON_PREFERENCE=only-system`, so the repository's `.venv` uses the shell's Python 3.11, and the GCC runtime on `LD_LIBRARY_PATH` for the PyTorch wheels.

If a wheel still fails to load a library on NixOS, use the FHS environment above. On Apple Silicon, `nix develop` gives a plain toolchain shell (`bun`, Node 22, `uv`, Rust, `ffmpeg`) and setup works as described in the [macOS guide](macos.md).

A backend-only shell is available as `nix develop .#backend` (`bun run setup:api && bun run dev:api`).

## Nix-built app (Linux x64)

```bash
nix build   # the unpacked Linux app in ./result
nix run .   # launch it straight from this checkout
```

- **First run installs Python like the AppImage does.** The app downloads uv's managed CPython into its data folder. That interpreter needs a standard ELF loader, so on NixOS enable [nix-ld](https://github.com/nix-community/nix-ld) (`programs.nix-ld.enable = true;`) or run from source in the FHS environment instead.
- **OmniVoice GGUF runtime included.** The package builds the pinned `omnivoice.cpp` runtime (Vulkan and CPU) and its `bin/checksums.sha256` manifest, as the installers ship them.
- **Updates come from Nix.** The package is a read-only store path, so its launcher sets `VOICESTUDIO_DISABLE_UPDATER=1`; update it with Nix.
- **No installer artifacts.** `bun run dist` cannot run under Nix: electron-builder downloads its own tool binaries (the AppImage runtime and `fpm`) from GitHub at build time, which the Nix sandbox blocks. Release AppImage and `.deb` artifacts come from CI.

`nix flake check` builds the app and verifies the packaged resources, the dictation helper's `libxdo` link, the launcher and the pruned runtime `node_modules`.

## Building and Testing

Inside the development shell, you can compile and test the native components:

```bash
# Run tests for the native Rust desktop bridge
cargo test --manifest-path native/desktop-bridge/Cargo.toml

# Check types and packaging contracts
bun run check:electron
```
