# VoiceStudio — Running and Developing with Nix / NixOS

The repository includes a community-maintained Nix flake (`flake.nix`) with development shells and, on Linux, an FHS environment that provides the native libraries VoiceStudio needs. It is a way to build and run from source; it is not exercised by CI, and the release installers remain the supported install path.

## Prerequisites

- [Nix](https://nixos.org/download.html) with [flakes enabled](https://nixos.wiki/wiki/Flakes#Enable_flakes).

## Getting Started

### 1. NixOS / FHS Environment (Recommended for NixOS)

On NixOS, dynamically loaded Python packages (such as the PyTorch and WhisperX wheels `uv` installs) expect standard Linux filesystem paths (`/lib64`, `/usr/lib`). The flake's FHS environment (Linux only) provides them, and makes `uv` use its own managed CPython:

```bash
# Clone the repository
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio

# Enter the FHS development environment
nix develop .#fhs
# or enter directly via nix run:
nix run .#fhs

# Inside the environment, install dependencies and launch the app
bun run dev
```

### 2. Standard Development Shell (macOS or Linux with nix-ld)

If you are developing on macOS or Linux with `nix-ld` configured:

```bash
# Clone the repository
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio

# Enter the standard development shell
nix develop

# Install dependencies and launch the app
bun run dev
```

The `nix develop` shell provides:
- **Toolchains**: `bun`, `nodejs_22`, `uv`, `rustc`, `cargo`, `pkg-config`.
- **System & multimedia libraries**: `alsa-lib`, `xdotool`, `libxtst`, `libx11`, `libxkbcommon`, `wayland`, `openssl`, and `ffmpeg`.
- Preconfigured `LD_LIBRARY_PATH` and `PKG_CONFIG_PATH` hooks for native compilations.

## Building and Testing

Inside either shell, you can compile and test the native components:

```bash
# Run tests for the native Rust desktop bridge
cargo test --manifest-path native/desktop-bridge/Cargo.toml

# Check types and packaging contracts
bun run check:electron
```
