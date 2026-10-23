# VoiceStudio — Running and Developing with Nix / NixOS

VoiceStudio provides an official Nix Flake (`flake.nix`) that supplies reproducible development shells and an FHS environment with all native dependencies configured for Nix and NixOS users.

## Prerequisites

- [Nix](https://nixos.org/download.html) with [flakes enabled](https://nixos.wiki/wiki/Flakes#Enable_flakes).

## Getting Started

### 1. NixOS / FHS Environment (Recommended for NixOS)

On NixOS, dynamically loaded Python packages (such as PyTorch and WhisperX wheels managed by `uv`) expect standard Linux filesystem paths (`/lib64`, `/usr/lib`). The flake provides an FHS environment that handles this transparently:

```bash
# Clone the repository
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio

# Enter the FHS development environment
nix develop .#fhs
# or enter directly via nix run:
nix run .#fhs

# Inside the environment, install dependencies and launch the app
bun install
bun run setup:api
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
bun install
bun run setup:api
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
