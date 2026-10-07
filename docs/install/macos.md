# VoiceStudio — Install on macOS

VoiceStudio's macOS desktop app is built with Electron. The archived Tauri app
is no longer maintained; if you still run it, follow the
[migration guide](../electron-migration.md).

> [!IMPORTANT]
> **Intel Macs are not supported** for the local backend (app UI only).
> PyTorch stopped shipping Intel-Mac (macOS
> x86_64) wheels after 2.2.x and VoiceStudio's dependencies need a newer torch,
> so a local Python backend cannot install on an Intel Mac — from the DMG *or*
> from source ([#889](https://github.com/debpalash/VoiceStudio/issues/889)).
> On Intel, packaged setup stops before creating a Python environment or
> downloading dependencies and shows remote-backend guidance instead: use the
> remote connection controls on the setup screen to point the app at a backend
> on another machine, or run VoiceStudio on an Apple Silicon Mac, Windows, or
> Linux.

## Requirements

- **macOS 13.3 (Ventura) or newer** on Apple Silicon (Intel: UI only, see the
  note above).
- **~10 GB free disk** for the app, its Python environment, and model weights.

GPU acceleration (Apple MPS) is automatic on Apple Silicon. Python, FFmpeg,
and model weights are bootstrapped by the app itself on first launch; no
toolchain is needed for the packaged app.

FFmpeg/FFprobe and yt-dlp are **not** prerequisites on any install path: the
app resolves them itself (a static build ships with the Python environment;
if nothing resolves, the app downloads its own checksummed build on first
run). Power users can inspect or override the binaries in
**Settings → Audio tools** — including pointing at a Homebrew copy.

Optional but recommended: **a Hugging Face account** for diarization and the
larger TTS models. See [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Install (DMG)

Download the DMG that matches your Mac (check **Apple menu → About This Mac →
Chip/Processor**) from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest),
double-click to mount it, and drag **VoiceStudio.app** into `/Applications`.

| Mac | DMG to download |
|-----|-----------------|
| Apple Silicon (M1/M2/M3/M4…) | `VoiceStudio-Electron-<version>-mac-arm64.dmg` |
| Intel | `VoiceStudio-Electron-<version>-mac-x64.dmg` — **UI only**: the local backend cannot run on Intel ([#889](https://github.com/debpalash/VoiceStudio/issues/889)) |

The architectures are **not** interchangeable: an Intel Mac cannot run the
`arm64` build (Rosetta 2 only translates the other direction). An Apple
Silicon Mac *can* open the `x64` build through Rosetta, but its local backend
then needs the Intel-only PyTorch wheels that no longer exist, so setup stops
and offers the Apple Silicon download instead. Install the `arm64` DMG over it;
your voices and projects are kept. Compare the download with the release's
`SHA256SUMS.txt` before opening it.

Or install the latest release from Terminal with the
[shell installer](script.md), which picks the right DMG, verifies its
checksum, and installs `VoiceStudio.app` into `/Applications` (or
`~/Applications` when `/Applications` is not writable):

```bash
curl -fsSL https://voicestudio.sh/install | sh
```

If the first launch is blocked by macOS Gatekeeper ("VoiceStudio cannot be
opened because the developer cannot be verified"), see the next section — it
opens with one right-click, no Terminal.

## App is "damaged" / can't be opened (Gatekeeper)

<a id="gatekeeper-quarantine"></a>

An unsigned or ad-hoc signed installer may show **"VoiceStudio cannot be opened
because the developer cannot be verified"**. macOS Gatekeeper cannot verify an
Apple Developer identity until the Electron release is signed and notarized
(#1779). For an installer from the official GitHub release, compare its SHA-256
with the release's `SHA256SUMS.txt` before considering the workaround below.
Restricted environments that prohibit the workaround need a signed, notarized
release; bypassing Gatekeeper is not a substitute.

**Fix — GUI, no Terminal (do this):** in Finder, **right-click** (or
Control-click) **VoiceStudio.app** → **Open** → click **Open** again in the
dialog. (On macOS 15 Sequoia: double-click once, then go to **System Settings →
Privacy & Security**, scroll down, and click **"Open Anyway"**.) This is a
one-time confirmation per install; afterwards it launches by double-click.

> If you instead see the harsher **"app is damaged and can't be opened. Move to
> Trash"** with no Open option, the download was corrupted or it's a pre-signing
> build — re-download the latest release, or use the Terminal fallback below.

**Fix — Terminal:** after dragging the app into `/Applications`, run:

```bash
xattr -dr com.apple.quarantine "/Applications/VoiceStudio.app"
```

(Adjust the path if you put the app somewhere other than `/Applications`.)

That clears the quarantine attribute so Gatekeeper stops blocking the launch — a
one-time fix per install.

### For maintainers — enabling notarized Electron builds

The maintained Electron release workflow uses a Developer ID Application
certificate (`ELECTRON_MACOS_CSC_LINK` and `ELECTRON_MACOS_CSC_KEY_PASSWORD`)
plus `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID` for
notarization. The Apple password is an **app-specific password**, not the
account login password. See [the release guide](../RELEASING.md#credentials)
for all platform credentials and verification gates. An Apple Developer
membership is required; the archived Tauri workflow's `APPLE_CERTIFICATE`
secrets do not sign Electron installers.

## Building from source

Use this for development or to run current `main`. To build and install a
desktop package from `main` without a checkout, use the shell installer's
[`--main` mode](script.md#building-main). Local source builds are not
local-backend capable on Intel Macs either.

Prerequisites (use a native arm64 terminal on Apple Silicon):

- **Xcode Command Line Tools** — `xcode-select --install` (includes **git**
  and the C toolchain; `curl` ships with macOS).
- **Bun** — `curl -fsSL https://bun.sh/install | bash`.
- **uv** — `curl -LsSf https://astral.sh/uv/install.sh | sh` (it provides the
  managed Python 3.11 the backend uses).
- **Node.js 22+** and **Rust / Cargo** (`brew install rust`, or rustup —
  reopen the terminal or source `"$HOME/.cargo/env"` afterwards) for the
  Electron native helper.

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run setup:api   # create the Python environment with uv
bun run dev         # Electron with hot reload; it starts and supervises the backend
```

Use `bun run desktop-prod` to build and launch the production bundle, or
`bun run dist` to create local DMG/zip packages in `electron/release/` without
publishing. Local packages are unsigned. See
[Electron setup](../../electron/README.md) for backend configuration.

## Apple Silicon vs Intel

- **Apple Silicon (M-series):** VoiceStudio automatically picks the `mlx-whisper`
  and `mlx-audio` backends where available — these use the Apple Neural Engine
  and Metal Performance Shaders for ~2× the throughput of the CPU path.
  Installing the **Parakeet TDT v3 (MLX)** model from **Model Catalogue** (ASR tab → the engine's **Weights**)
  additionally makes dictation/capture prefer the `parakeet-mlx` engine
  (25 European languages, word timestamps, ~2 GB unified memory) — it is never
  downloaded without that explicit install, and it is only auto-preferred when
  your system language is one of its 25 covered languages (other languages —
  CJK, Arabic, … — keep the multilingual Whisper engine so dictation coverage
  never regresses; pin `ASR_MODEL_PARAKEET_MLX` to force it).
- **Intel Macs:** the local backend is **unsupported** — PyTorch no longer
  ships Intel-Mac wheels, so the Python environment can never install
  ([#889](https://github.com/debpalash/VoiceStudio/issues/889)). The UI
  works only when connected to a remote backend.

The picker in **Model Catalogue** shows which backend is active.

## Hugging Face token (optional but recommended)

The default install works without a token, but diarization (the
`pyannote/speaker-diarization-3.1` model) is gated and the larger
voice-design engines also download faster with a token attached.

- Open **Settings → API Keys** in the app.
- Or set the env var `export HF_TOKEN=hf_…` in `~/.zshrc`.

Full details: [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Troubleshooting

Hit a wall? See [docs/install/troubleshooting.md](troubleshooting.md).

The in-app error UI includes an **"Open docs for this error"** button that
deeplinks back into this docs tree at the right section for the error class.

### `torch` "doesn't have a source distribution or wheel for the current platform"

If setup fails with `You're on macOS (macosx_…_x86_64), but torch … only has
wheels for … macosx_11_0_arm64`, the runtime is being built for Intel. On an
Apple Silicon Mac that means the `x64` DMG (or a Rosetta terminal for source
installs) is in use: install the `mac-arm64` DMG, or run `bun run setup:api`
from a native arm64 terminal (`uname -m` prints `arm64`). On an Intel Mac the
local backend is unsupported; connect to a remote backend instead.

### Fast process shutdown

A process that exits while shutdown is signalling it can report a macOS
permission error. VoiceStudio accepts this only after confirming the original
process exited without being reaped (macOS can take a moment to report that
exit, so it waits up to a quarter of a second), then still waits for nested
operations to drain. Live-process permission errors and lost process ownership remain failures.
