# VoiceStudio — Running and Developing with Nix / NixOS

The repository includes a community-maintained Nix flake (`flake.nix`) with development shells for building and running from source. It is not exercised by CI, and the release installers remain the supported install path.

## Prerequisites

- [Nix](https://nixos.org/download.html) with [flakes enabled](https://nixos.wiki/wiki/Flakes#Enable_flakes).

## Getting Started

### Development Shell

```bash
# Clone the repository
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio

# Enter the development shell
nix develop

# Install dependencies, create the Python venv, and launch the app
bun install
bun run setup:api
bun run dev
```

The `nix develop` shell provides:
- **Toolchains**: `bun`, `nodejs_22`, `uv`, `python311`, `rustc`, `cargo`, `clang`, `cmake`, `pkg-config`.
- **Libraries**: `alsa-lib`, `libxdo` (built from xdotool's sources), `libXtst`, `libx11`, `libxkbcommon`, `wayland`, `openssl`, `ffmpeg`, plus the Electron runtime libraries (`libGL`, `nss`, `at-spi2-atk`, `libdrm`, `libgbm`, `libpulseaudio`, `libjack2`, `dbus`).
- Preconfigured `LD_LIBRARY_PATH` (GCC runtime) and `NIX_LDFLAGS` (libxdo) for native compilations.

A backend-only shell is available as `nix develop .#backend`.

On NixOS, dynamically loaded Python wheels (PyTorch, WhisperX) may look for
libraries in standard paths such as `/lib64` and `/usr/lib`. The shell puts the
GCC runtime on `LD_LIBRARY_PATH`; if a wheel still fails to load a library, add
that library's path there or run the backend in Docker.

## Building and Testing

Inside the development shell, you can compile and test the native components:

```bash
# Run tests for the native Rust desktop bridge
cargo test --manifest-path native/desktop-bridge/Cargo.toml

# Check types and packaging contracts
bun run check:electron
```
