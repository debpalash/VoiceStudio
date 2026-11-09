# Bundled binaries

This directory holds the paths for platform-specific native binaries. The files
in git are zero-byte placeholders; CI builds the real binaries
(`.github/workflows/build-omnivoice-tts.yml`), and the desktop installers ship
them under `resources/bin/` (#2780; not on Intel Macs, which cannot run the
local backend). Today:

| File | Built from | Purpose |
|------|------------|---------|
| `omnivoice-tts-darwin-arm64` | `ServeurpersoCom/omnivoice.cpp` @ pinned SHA | GGUF inference runtime — Apple Silicon |
| `omnivoice-tts-darwin-x86_64` | same | Intel Mac (local builds only; not shipped) |
| `omnivoice-tts-linux-x86_64` | same | Linux (x86_64) |
| `omnivoice-tts-linux-aarch64` | same | Linux ARM64 — Apple Silicon under Asahi; built with GGML Vulkan where the toolchain supports it, so the Honeykrisp driver can accelerate generation |
| `omnivoice-tts-windows-x86_64.exe` | same | Windows |
| `checksums.sha256` | computed by `scripts/build-omnivoice-tts.sh` | SHA-256 manifest — verified by `VoiceStudioGGUFBackend.is_available()` |

The pinned commit SHA for `omnivoice.cpp` lives in
`backend/engines/omnivoice_gguf/quant_map.json` `_meta.runtime_commit_sha`.

## Building locally

```
scripts/build-omnivoice-tts.sh --platform <slug> --commit-sha <40hex>
```

See `.github/workflows/build-omnivoice-tts.yml` `build-omnivoice-tts` job
for the CI matrix that produces these artifacts. Apple Silicon (`macos-14`)
builds cleanly with `-DGGML_METAL=ON` at the pinned SHA (#2105), enabling
hardware-accelerated Metal inference when the packaged artifact passes binary
preflight and macOS permits execution. Missing or blocked binaries retain the
in-process `VoiceStudioBackend` fallback.

## Placeholder note

Until the CI matrix produces real binaries, this directory contains
zero-byte placeholders — a plain `git clone` always gets those (real
binaries ship via the installers / CI artifacts, and are never committed
here). `VoiceStudioGGUFBackend.is_available()` validates the file before
trusting it (`services/binary_preflight.py`: non-empty + a real
Mach-O/ELF/PE magic, #1172) and returns `(False, "...not a usable
executable...")` for a placeholder, so the engine reports honestly
through the Engine Compatibility Matrix and the default selection falls
back to the in-process `VoiceStudioBackend`. Selecting the engine anyway
(e.g. `model: "omnivoice-gguf"` on `/v1/audio/speech`) yields an
actionable 400/503 naming `scripts/build-omnivoice-tts.sh`, never a raw
"Exec format error".
