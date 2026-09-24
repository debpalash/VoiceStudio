# VoiceStudio — Install on macOS

## Electron desktop (current)

From the repository root, install Bun and uv, then run:

```sh
bun install
bun run setup:api  # prepare Python dependencies before starting Electron
bun run dev
```

Use `bun run desktop-prod` to build and launch Electron, or `bun run dist`
to create local installers without publishing. The app manages its backend.
See [Electron setup](../../electron/README.md) and [migration notes](../electron-migration.md).

## Legacy Tauri installation and troubleshooting

The instructions below apply to the sunset Tauri app and existing Tauri installers.

This page is self-contained: follow it top to bottom and you'll end up with a
working VoiceStudio install on macOS (Apple Silicon).

> [!IMPORTANT]
> **Intel Macs are not supported.** The app UI installs and launches, but the
> local Python backend **cannot run**: PyTorch stopped shipping Intel-Mac
> (macOS x86_64) wheels after 2.2.x, and VoiceStudio's dependencies require a
> newer torch — so the first-run dependency install can never succeed, from
> the DMG *or* from source
> ([#889](https://github.com/debpalash/VoiceStudio/issues/889)). The app
> detects this at first launch and tells you directly instead of failing with
> a raw installer error. Your options on an Intel Mac: point the UI at a
> remote backend running on another machine (**Settings → Sharing → Remote
> backend**), or run VoiceStudio on an Apple Silicon Mac, Windows, or Linux.

## Prerequisites

### Using the DMG

- **macOS 13.3 (Ventura) or newer** — Apple Silicon (Intel: UI only, see the
  note above).
- **~10 GB free disk** for the app, its Python environment, and model weights.

That's it — GPU acceleration (Apple MPS) is automatic on Apple Silicon, and
Python, FFmpeg, and the model weights are bundled or bootstrapped by the app
itself on first launch. No toolchain needed.

### Building from source

Everything above, plus the toolchain:

- **Xcode Command Line Tools** — `xcode-select --install` (includes **git**
  and the C toolchain; `curl` ships with macOS).
- **Python 3.11+** — `brew install python@3.11` (or use `pyenv` / the system Python if you already have ≥3.11).
- **Bun** — `curl -fsSL https://bun.sh/install | bash`.
- **Rust / Cargo** — `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` or `brew install rust`.
  If you use rustup, reopen the terminal or source `"$HOME/.cargo/env"` before running `bun run tauri:desktop-prod`.

FFmpeg/FFprobe and yt-dlp are **not** prerequisites on any install path: the
app resolves them itself (a static build ships with the Python environment;
if nothing resolves, the app downloads its own checksummed build on first
run). Power users can inspect or override the binaries in
**Settings → Audio tools** — including pointing at a Homebrew copy.

Optional but recommended:

- **A Hugging Face account** for diarization and the larger TTS models. See
  [docs/setup/huggingface-token.md](../setup/huggingface-token.md).

## Install (from source)

One-liner (installs prerequisites, clones, and builds):

```bash
curl -fsSL https://voicestudio.sh/install | sh
```

Or manually:

```bash
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run tauri:desktop-prod
```

The first launch builds the Tauri shell, creates the Python venv via `uv`,
syncs deps, and downloads model weights (~2.4 GB). The splash screen shows
live progress for every step.

## Install (pre-built `.app`)

Download the latest DMG from the
[Releases page](https://github.com/debpalash/VoiceStudio/releases/latest),
double-click to mount, drag **VoiceStudio.app** into `/Applications`.

Pick the DMG that matches your Mac (check **Apple menu → About This Mac → Chip/Processor**):

| Mac | DMG to download |
|-----|-----------------|
| Apple Silicon (M1/M2/M3/M4…) | `VoiceStudio.Studio_<version>_aarch64.dmg` |
| Intel | `VoiceStudio.Studio_<version>_x64.dmg` — **UI only**: the local backend cannot run on Intel ([#889](https://github.com/debpalash/VoiceStudio/issues/889)) |

The architectures are **not** interchangeable: an Intel Mac cannot run the
`aarch64` build (Rosetta 2 only translates the other direction — it lets Apple
Silicon run Intel apps, never the reverse). And note the Intel caveat above:
the `x64` DMG installs and launches, but is only useful together with a
remote backend — the local Python backend cannot install on Intel because
PyTorch no longer ships Intel-Mac wheels. Installing from source does not
help; the dependency resolution fails the same way.

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
  works only when pointed at a remote backend (**Settings → Sharing → Remote
  backend**).

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

The in-app error UI (the React error boundary that fires on backend errors)
includes an **"Open docs for this error"** button — that button deeplinks
back into this docs tree at the right section for the error class.

### Desktop window chrome

The main window uses native macOS traffic lights with an overlay title bar;
window sizing, resize limits, and application file-drop behavior match the
other desktop platforms. The platform configuration repeats the complete window
list because Tauri replaces arrays when merging it with the shared config.
The capture widget remains a separate borderless window created at runtime.
Its window-scoped Tauri capability permits hiding after recording or idle
reconciliation on every desktop platform.

### Fast process shutdown

A process that exits while shutdown is signalling it can report a macOS
permission error. VoiceStudio accepts this only after confirming the original
process exited without being reaped (macOS can take a moment to report that
exit, so it waits up to a quarter of a second), then still waits for nested
operations to drain. Live-process permission errors and lost process ownership remain failures.
