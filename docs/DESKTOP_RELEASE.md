# Desktop release plan — VoiceStudio

> **Historical/manual-only Tauri plan.** Everything below is retained solely to
> audit or manually reconstruct the final v0.5.3 Tauri release. Its build,
> signing, and publication instructions are not maintained and must not be used
> for a current release. Electron is the only maintained desktop app; follow
> [RELEASING.md](RELEASING.md) for all current build and publication work.

A shippable macOS (and eventually cross-platform) desktop release where the user drags the `.app` to `Applications`, double-clicks once, and does **everything else from the UI** — dependency runtime, model weights, first-run consent, all inside the app.

Stack: Tauri v2 + FastAPI sidecar + PyInstaller. Target: ~500 MB signed + notarized arm64 DMG, with matching Windows `.msi`/`.nsis` later. Large optional payloads (CUDA libs, extra model packs) ship as separate lazy-download tarballs, not in the base DMG.

---

## Target architecture

| Layer | Contents | Ships in DMG? |
|---|---|---|
| Tauri v2 shell (Rust) | Native window, process lifecycle, filesystem paths | Yes |
| Frontend bundle | React/Vite build in `.app/Contents/Resources/dist/` | Yes |
| **FastAPI sidecar binary** | **PyInstaller-frozen** `omnivoice-backend` with Python + torch + mlx + soundfile + demucs + yt_dlp + omnivoice TTS | Yes (~400–500 MB bundle) |
| ffmpeg | arm64 binary in `.app/Contents/Resources/bin/` | Yes (~20 MB) |
| Model weights (VoiceStudio TTS, MLX Whisper) | `~/Library/Application Support/OmniVoice/models/` | **No — first-run download** |
| Optional engine packs (VoxCPM2 CUDA, pyannote, MOSS-TTS) | Separate `.tar.gz` via GitHub Releases manifest | **No — first-run download if user opts in** |

Target DMG size: **~500 MB**.
First-run model download: **~2.4 GB** one-time (the TTS model is the only required download; ASR/transcription models are optional per-platform curated picks installed on demand from the wizard or Settings).

---

## Four key techniques

### 1. Sidecar port-reuse dance (dev ergonomics + crash recovery)

Tauri's startup flow:

1. Check if `127.0.0.1:17493/health` responds.
2. If yes, verify JSON shape: `status == "healthy"`, `model_loaded: bool`, `gpu_available: bool`. If valid, **attach** to the existing process instead of spawning.
3. If a legacy port (8000) has an orphan, kill via `lsof -ti :8000 | xargs kill -9`.
4. Otherwise spawn the frozen backend sidecar via Tauri's `externalBin`.
5. On app close: send SIGTERM, wait 2 s, SIGKILL if still alive.

**Why this matters:** you can still `uv run uvicorn …` in dev and Tauri cooperates. Restarting a crashed backend is a port-probe, not a process-kill dance.

**Our files to touch:** `frontend/src-tauri/src/lib.rs` (replace current `find_project_root` / `uv run` logic with port-probe + `externalBin` launch).

### 2. tqdm → SSE progress for HuggingFace downloads

Our `backend/utils/hf_progress.py` is ~80 lines:

```python
from huggingface_hub.utils import _tqdm as hf_tqdm_module
from tqdm.auto import tqdm as base_tqdm

class TrackedTqdm(base_tqdm):
    def update(self, n=1):
        super().update(n)
        callback(self.desc or "download", self.n, self.total)

# Monkey-patch once at startup — every hf_hub_download() across every
# library (transformers, diffusers, accelerate, mlx_whisper) now reports.
hf_tqdm_module._original_tqdm_class = hf_tqdm_module.tqdm_class
hf_tqdm_module.tqdm_class = TrackedTqdm
```

Pipe callbacks to a new `/setup/download/stream` SSE endpoint. React subscribes with `EventSource`, renders per-file progress bars, locks the rest of the UI until models are ready.

**Zero changes to calling code.** Every `mlx_whisper.load_model(...)` now reports progress for free.

**Our files to add:** `backend/utils/hf_progress.py` + `backend/api/routers/setup.py` with `/setup/status` and `/setup/download/stream` endpoints. Frontend `src/pages/SetupWizard.jsx` that renders when `/setup/status` says models aren't present.

### 3. Two-tier binary: small base DMG + lazy optional payloads

We exclude every `nvidia.*` wheel from the Apple Silicon build (saves ~2 GB) and ship CUDA libs in a separate `cuda-libs-cu128-v1.tar.gz` (~2 GB), referenced by `cuda-libs.json` on the release.

For us:
- **Base DMG ships MPS + MLX path only.** Excludes `nvidia.*`, `triton`, `flash-attn`, anything CUDA-specific in the spec.
- **Optional pack: VoxCPM2** (requires CUDA). Not installed by default. Model Catalogue → "Install VoxCPM2" triggers download from our `voxcpm2-cu128-v1.tar.gz` release asset.
- **Optional pack: pyannote** (HF-token gated). Default off. Settings → Speaker diarisation → "Enable" prompts for HF token, downloads + installs.
- **Optional pack: MOSS-TTS-Nano.** Same pattern.

Manifest format for `cuda-libs.json`:
```json
{
  "url": "https://github.com/.../releases/download/v0.1.0/voxcpm2-cu128-v1.tar.gz",
  "sha256": "…",
  "size_bytes": 2100000000,
  "extract_to": "packs/voxcpm2"
}
```

### 4. PyInstaller spec + runtime hooks

Starting point: our existing `backend.spec` (already rewritten this session).

Two runtime hooks we need:

- **`pyi_rth_numpy_compat.py`** — fixes a numpy compat shim that PyInstaller misses.
- **`pyi_rth_torch_compiler_disable.py`** — disables `torch.compile` code paths that break under frozen imports.

Exclude list (saves space on Apple Silicon build):
```python
excludes = [
    'nvidia.cublas', 'nvidia.cudnn', 'nvidia.cuda_runtime',
    'nvidia.nccl', 'nvidia.nvtx',
    'triton', 'flash_attn',
    'tkinter', 'matplotlib.backends._tkagg',
]
```

Hidden-imports to add (iterative — fix as PyInstaller errors surface):
- `mlx.core`, `mlx.nn`
- `omnivoice`, `omnivoice.models.omnivoice`
- `soundfile._soundfile`
- `demucs.separate`, `demucs.pretrained`
- `huggingface_hub.repocard_data`

---

## Phased execution plan

Each phase produces a testable artifact. Don't proceed to the next phase until the current one verifies end-to-end.

### Phase A — Frozen backend works (3–5 h, highest risk)

**Deliverable:** `dist/omnivoice-backend/omnivoice-backend` runs standalone + serves the full API.

1. Add two runtime hooks to `backend/hooks/`.
2. Update `backend.spec` with the exclude list + the runtime hook paths.
3. Run `uv run pyinstaller backend.spec --noconfirm --clean`.
4. Iterate on hidden-imports until `./dist/omnivoice-backend/omnivoice-backend` starts cleanly and `/system/info` returns 200.
5. End-to-end smoke: transcribe the Fireship fixture → generate dub in Spanish → verify output audio exists.

**Verify:** `curl -sf http://127.0.0.1:17493/system/info` on the frozen binary returns JSON in <2 s.

**Fail-path:** if PyInstaller can't bundle after 5 hours, pivot to "ship a portable `.venv` inside `.app/Contents/Resources/`" — uglier, reliably works. Adds ~300 MB but skips PyInstaller drama.

### Phase B — Tauri launches the frozen sidecar (2 h)

**Deliverable:** `bun run desktop` launches a dev .app that uses the frozen backend, not `uv run`.

1. Rewrite `frontend/src-tauri/src/lib.rs`'s `setup` hook:
   - Check port 17493 first (port-reuse dance).
   - If free, launch the bundled `Contents/Resources/backend/omnivoice-backend` via Tauri's `shell_plugin::Command`.
   - Kill orphans on port 8000 (legacy).
2. Wire `tauri.conf.json` `bundle.resources` to include `../../dist/omnivoice-backend/**` and `binaries/ffmpeg`.
3. Change backend's default port from 8000 → 17493 (new namespace, fewer conflicts with other dev tools).

**Verify:** launch the dev app with `bun run desktop` — window opens, segment table loads, test ingest-url works.

### Phase C — First-run model download UI (4–6 h)

**Deliverable:** fresh app on a machine with no cached HF models walks user through download with live progress.

1. Implement `hf_progress.py` — ~80 LOC.
2. New `backend/api/routers/setup.py`:
   - `GET /setup/status` → `{ models_ready: bool, missing: [...], disk_free_gb: number }`.
   - `GET /setup/download/stream` → SSE: `{ type: "progress", file, bytes, total, pct }` then `{ type: "done" }`.
3. Frontend `src/pages/SetupWizard.jsx`:
   - Shown when `/setup/status` says models missing.
   - Per-file progress bars driven by the SSE stream.
   - Disk-space check; error state if <10 GB free.
   - Retry on network failure.
4. App-level route guard: if `setupWizardNeeded`, render `<SetupWizard>` instead of `<Launchpad>`.

**Verify:** move/rename `~/Library/Application Support/OmniVoice/models/` — launch app — wizard shows up — progress bars tick — models download — UI unlocks.

### Phase D — DMG build + clean-machine test (2–3 h)

**Deliverable:** signed-but-not-notarized DMG that works on a virgin Mac after right-click → Open.

1. `bun run tauri build` (via `scripts/build_desktop.sh` we'll add).
2. Artifact: `frontend/src-tauri/target/release/bundle/dmg/VoiceStudio_0.1.0_aarch64.dmg`.
3. Copy to a fresh macOS user account (or a second Mac).
4. Right-click → Open once, walk the wizard, dub the Fireship fixture.
5. Fix whatever breaks.

**Verify:** target Mac with NO development tools installed can dub a YouTube URL in the target language end-to-end.

### Phase E — (optional) Signed + notarized

**Deliverable:** DMG that opens without Gatekeeper override.

Requires:
- Apple Developer ID (~$99/yr).
- Code-signing cert, App Store Connect API key.
- GitHub Actions workflow (`.github/workflows/release.yml`):
  - `apple-actions/import-codesign-certs@v3`
  - `tauri-apps/tauri-action@v0.6` with `APPLE_SIGNING_IDENTITY` + `APPLE_API_KEY` + `APPLE_API_ISSUER` + `APPLE_PROVIDER_SHORT_NAME`
  - **Explicit DMG re-notarize step** — macOS 15 Sequoia rejects DMGs that wrap a signed `.app` but aren't themselves notarized. Run `xcrun notarytool submit --wait` + `xcrun stapler staple` on the DMG, re-upload as release asset.

---

## Cross-platform extension (future)

Targets: macOS arm64 + macOS x64 + Windows x64 (`.msi`, `.nsis`, `.exe`). Linux is best-effort.

We can mirror this by extending the CI matrix once Phases A–D are green:

| Target | Runner | PyInstaller variant | Notes |
|---|---|---|---|
| macOS Apple Silicon | `macos-14` (ARM) | `backend.spec` (MPS/MLX) | Our primary |
| macOS Intel | `macos-13` | `backend.spec` (MPS/x64 torch) | MLX absent — falls back to CPU Whisper |
| Windows x64 (NVIDIA) | `windows-2022` | `backend.spec` + `--bootloader` + CUDA | Requires second CUDA tarball (`cuda-libs-cu128-v1.tar.gz`) |
| Linux x64 | `ubuntu-22.04` | `backend.spec` | Best-effort — untested |

Each platform's first build will take the longest (PyInstaller hidden-import tuning is per-OS). Subsequent builds reuse the spec.

**Honest caveat:** Windows is a whole separate set of headaches — `mlx_whisper` doesn't exist there, `pyannote` + `soundfile` have different wheel sources, signing requires a separate Windows code-signing cert. Add **1 full session per additional platform**.

For our current goal (friend on the same M2 air), **stop at Phase D**. Cross-platform is a later conversation once macOS is solid.

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| PyInstaller can't bundle torch Metal libs | Medium | Use `collect_all(...)` calls for torch + MLX. Fallback: portable `.venv` approach |
| `torch.compile` breaks under frozen imports | Certain | `pyi_rth_torch_compiler_disable.py` runtime hook |
| DMG size >800 MB | Medium | Keep nvidia/triton/matplotlib out of the spec; defer optional packs to lazy download |
| First-run download fails halfway | Medium | SSE retry + resumable `hf_hub_download` (supported natively). Show disk-space check upfront |
| Gatekeeper blocks unsigned app | Certain | Document right-click → Open as the one-time step. Long-term: buy Apple Developer ID |
| User's friend has <10 GB free | Low | Pre-check in `/setup/status`. Refuse to start download if insufficient. Point user to clear space |
| Apple Silicon build runs on Intel Mac | Possible | Warn in installer + app header. Don't promise cross-arch without actual Intel build |

---

## Success criteria (for this plan, per phase)

- **A ✅ when** `./dist/omnivoice-backend/omnivoice-backend` starts in <3 s on a clean shell and serves `/system/info`.
- **B ✅ when** `bun run desktop` launches a window that uses the frozen binary (not `uv run`) and all core APIs work.
- **C ✅ when** deleting the models dir and launching shows a wizard that completes to functional state without any terminal interaction.
- **D ✅ when** an unrelated M-series Mac runs the DMG end-to-end (Fireship clip → Spanish dub) with zero developer tooling installed, just right-click → Open once.

---

## Key implementation files

- `backend.spec` — PyInstaller spec
- `backend/utils/hf_progress.py` — tqdm monkey-patch for download progress
- `backend/hooks/pyi_rth_numpy_compat.py` — numpy runtime hook
- `backend/hooks/pyi_rth_torch_compiler_disable.py` — torch.compile disable
- `frontend/src-tauri/src/lib.rs` — sidecar spawn + port-reuse
- `frontend/src-tauri/tauri.conf.json` — bundle + updater config
- `scripts/build_desktop.sh` — build entry
- `.github/workflows/release.yml` — full release pipeline (signing, notarization, DMG re-notarize)

---

## Out-of-scope for v1

- Auto-update (Tauri has `tauri-plugin-updater`, but requires signing + hosted `latest.json`).
- Automatic crash reporting (needs a Sentry-type endpoint).
- User telemetry of any kind.
- In-app feedback form.
- Notarized installer — Phase E, deferred.
- Cross-platform builds — see "Cross-platform extension" section.
- Homebrew cask — possible later, not blocking.

---

## Timeline (honest, single developer)

| Phase | Hours | Confidence |
|---|---|---|
| A — frozen backend | 3–5 | High |
| B — Tauri sidecar + port-reuse | 2 | High |
| C — first-run wizard + hf progress | 3–4 | High (80 LOC + UI) |
| D — DMG + clean-machine test | 2–3 | Medium (Gatekeeper dance) |
| **Total (macOS arm64 only)** | **10–14** | **Phaseable over 2–3 sessions** |
| E — signing + notarization | +3 | Blocked on Apple Developer ID |
| Cross-platform (each OS) | +8 | Per-OS effort |
