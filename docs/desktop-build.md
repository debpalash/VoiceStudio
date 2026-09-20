# Desktop build — progress tracker

> **Archived Tauri plan.** This document records the retired v0.5.3 shell.
> Current desktop work lives in `electron/` and [Electron setup](../electron/README.md).

Working doc for the desktop release effort. Every box maps to a concrete
deliverable; mark `[x]` when verified end-to-end on a fresh environment,
not just "code compiles." See `docs/DESKTOP_RELEASE.md` for the full
engineering plan that grounds these milestones.

**Primary target:** macOS Apple Silicon (arm64), unsigned.
**Stretch targets:** macOS Intel, Windows x64, Linux — parked in CI matrix
until the arm64 path is green end-to-end.

---

## Source of truth files

| Concern | Owner file |
|---|---|
| Backend freeze spec | `backend.spec` |
| PyInstaller runtime hooks | `backend/hooks/*.py` (TBD) |
| Tauri sidecar launcher | `frontend/src-tauri/src/lib.rs` |
| Tauri bundle config | `frontend/src-tauri/tauri.conf.json` |
| HF download progress | `backend/utils/hf_progress.py` |
| First-run wizard endpoints | `backend/api/routers/setup.py` |
| First-run wizard UI | `frontend/src/pages/SetupWizard.jsx` (TBD) |
| CI release matrix | `.github/workflows/release.yml` |
| Reference implementation | — |

---

## Phase A — Frozen backend binary (✅ 2026-04-21)

- [x] Add runtime hooks to `backend/hooks/`
  - [x] `pyi_rth_numpy_compat.py` — pre-imports numpy to prime the C ext
  - [x] `pyi_rth_torch_compiler_disable.py` — disables dynamo/inductor via env
- [x] Wire hooks into `backend.spec` via `runtime_hooks=[...]`
- [x] Add Apple Silicon exclude list to `backend.spec` (`nvidia.*`, `triton`, `flash_attn`)
- [x] `uv run pyinstaller backend.spec --noconfirm --clean` produces a clean bundle — **140 s build time**
- [x] `./dist/omnivoice-backend/omnivoice-backend` starts and serves `/system/info` — **~23 s cold start** (not ≤3 s as targeted, but acceptable behind splash screen in Phase D)
- [x] Frozen binary serves all core endpoints (`/system/info`, `/setup/status`, `/engines`)
- [x] `hf_progress` patch installs on frozen start (confirmed via log)
- [ ] Frozen binary transcribes Fireship fixture → Spanish dub end-to-end (deferred to Phase B — needs Tauri WebView for the frontend, or direct `curl`-driven harness)
- [ ] **Bundle size ≤600 MB** — **currently 1.1 GB, ~2× over target.** Likely shavable to ~700 MB by excluding `torch.distributed.*`, transformers bloat, scipy tests. Not a Phase A blocker; cleanup tracked as Phase A.1.

**Verified:**
```bash
./dist/omnivoice-backend/omnivoice-backend
# → "Uvicorn running on http://0.0.0.0:8000"
curl -s http://127.0.0.1:8000/system/info          # ✓ returns JSON
curl -s http://127.0.0.1:8000/setup/status         # ✓ {"models_ready":true,...}
curl -s http://127.0.0.1:8000/engines              # ✓ omnivoice/voxcpm2/moss-tts-nano listed
```

**Lessons learned (feeding into Phase B):**
- PyInstaller buffers stdout by default — Tauri sidecar spawn must set `PYTHONUNBUFFERED=1` so logs surface promptly.
- `main.py`'s `if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)` is what makes the frozen binary serve. Keep it.
- `frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")` at `main.py:172` will never resolve in a frozen build; frontend assets come from Tauri's WebView instead, not the Python backend. Guard is fine; note for Phase B.

### Phase A.1 — Bundle size reduction (optional, before Phase D)

- [ ] Add `torch.distributed.*` to excludes (not used on Apple Silicon single-device inference)
- [ ] Exclude `scipy.special.tests.*`, `numpy.tests.*`, `torch.testing.*`
- [ ] Exclude `transformers.models.*` for non-used model families
- [ ] Target: <700 MB bundle

---

## Phase B — Tauri launches the frozen sidecar (in progress 2026-04-21)

- [ ] ~~Change backend default port 8000 → 17493~~ — deferred. Port-switching ripples through the frontend API client + bench scripts + install scripts. Keep 8000 for now; switch in a dedicated refactor when we need to.
- [x] Rewrite `lib.rs::setup` to:
  - [x] Probe `/system/info` on port 8000 and attach if responding (`backend_healthy()`)
  - [x] Kill orphans on port 8000 via `lsof -ti :8000 | xargs kill -9`
  - [x] Launch bundled `Contents/Resources/backend/omnivoice-backend/omnivoice-backend` if free
  - [x] Fall back to `uv run uvicorn ...` in dev (when bundled binary not present)
  - [x] Set `PYTHONUNBUFFERED=1` on sidecar env so backend logs flush in real time
  - [x] Export `OMNIVOICE_FFMPEG` pointing at bundled ffmpeg + prepend its dir to `PATH`
- [x] Wire `tauri.conf.json` `bundle.resources`:
  - [x] `../../dist/omnivoice-backend` → `backend/omnivoice-backend/`
  - [x] ~~`binaries/ffmpeg` → `bin/ffmpeg`~~ — **removed**. Hit `Permission denied (os error 13)` at bundle time (brew shim carried `com.apple.provenance` xattr that `xattr -c` couldn't strip under SIP). Dropped the separate resource: `imageio_ffmpeg` already ships a 47 MB static arm64 ffmpeg inside the PyInstaller bundle at `_internal/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1`, and `find_ffmpeg()` picks it up via `imageio_ffmpeg.get_ffmpeg_exe()` — zero extra bundling.
- [x] Rust compile clean — `cargo check` passes
- [x] `bunx tauri build` produces an `.app` (1.3 GB) — bundle contents verified: `Contents/Resources/backend/omnivoice-backend/omnivoice-backend`. ffmpeg travels inside the backend resource (see above).
- [x] `bunx tauri build` produces a `.dmg` — **400 MB** ✅ (well under the 600 MB target).
- [x] Fix API host in production build — `frontend/src/api/client.ts` previously used `API = ''` in prod, which caused relative fetches against `tauri://localhost` to fail with *"The string did not match the expected pattern"* in the Settings → Models tab. Now hardcodes `http://localhost:8000` so the webview always reaches the bundled sidecar. CORS + CSP already allow it.
- [ ] `.app` opens, connects to bundled backend, ingests/transcribes without error (manual test pending)

### DMG packaging failure to investigate

Tauri's `bundle_dmg.sh` runs `hdiutil` to convert the intermediate RW DMG → compressed read-only DMG. On our 1.7 GB payload this failed silently; next session's task is to run the shell script manually and capture stderr. Possible fixes in priority order:
1. Shrink the backend bundle (Phase A.1 — we're 1.1 GB, target is ~500 MB; cuts DMG pipeline latency and works around many hdiutil edge cases).
2. Pass `hdiutilArgs` in `tauri.conf.json` → `["-format", "UDZO", "-imagekey", "zlib-level=1"]` — cheaper compression, faster, fewer hdiutil quirks.
3. If hdiutil errors with disk space: `TMPDIR=/Volumes/other bunx tauri build` to route the scratch volume elsewhere.
4. Last resort: build `.app.tar.gz` with `create-dmg` externally.

**Verification target:**
```bash
open frontend/src-tauri/target/release/bundle/macos/"VoiceStudio.app"
# The window should open, segment table should render from a fresh drop.
```

---

## Phase C — First-run wizard + HF progress (✅ 2026-04-21)

- [x] **`backend/utils/hf_progress.py`** — tqdm monkey-patch
- [x] **`backend/api/routers/setup.py`**
  - [x] `GET /setup/status` — missing + disk-free
  - [x] `GET /setup/download-stream` — SSE stream
  - [x] `POST /setup/warmup` — background model load
  - [x] `GET /models` — every known model + install state (Phase M seed)
  - [x] `POST /models/install` — single-repo install (progress via SSE)
  - [x] `DELETE /models/{repo_id}` — evict cached revisions, free disk
- [x] `frontend/src/pages/SetupWizard.jsx` + `.css`
  - [x] Mounts on boot, calls `/setup/status`
  - [x] Per-file progress bars from SSE
  - [x] Disk-space error state if `disk_free_gb < min_free_gb`
  - [x] Polls `/setup/status` every 5 s during install to detect completion
- [x] Route guard in `App.jsx`: `if (setupNeeded) return <SetupWizard>`
- [x] Settings → Models tab: `ModelStoreTab` replaces the old read-only summary
  - [x] Lists every KNOWN_MODEL with status badge, role, size, repo_id
  - [x] Install / Reinstall / Delete buttons per row
  - [x] Aggregate "on-disk" footer
  - [x] Live per-file progress bars driven by the shared SSE stream

**Verification:**
```bash
# Simulate fresh install
mv ~/.cache/huggingface/hub /tmp/hf-hub.bak
bun run dev
# Wizard appears, progress ticks, UI unlocks when done.
mv /tmp/hf-hub.bak ~/.cache/huggingface/hub  # restore
```

---

## Phase D — DMG + clean-machine test

- [ ] `frontend/src-tauri/tauri.conf.json` `bundle.targets = ["dmg", "app"]`
- [ ] `scripts/build_desktop.sh` — one-shot: PyInstaller → Tauri build → DMG
- [ ] Build produces `VoiceStudio_0.1.0_aarch64.dmg`
- [ ] DMG size ≤600 MB
- [ ] DMG runs on a fresh macOS user account (no brew/uv/bun present)
  - [ ] Mount → drag to Applications → right-click → Open (Gatekeeper override)
  - [ ] First launch shows setup wizard, models download cleanly
  - [ ] End-to-end: ingest YouTube URL → transcribe → translate → generate → play
- [ ] README section: "Download DMG" with right-click → Open instructions

**Verification:**
```bash
bash scripts/build_desktop.sh
ls -lh frontend/src-tauri/target/release/bundle/dmg/*.dmg
# Copy to fresh user account via System Settings → Users → Add.
# Log in, install DMG, walk the whole flow.
```

---

## Phase E — Signing + notarization (blocked on Apple Developer ID)

- [ ] Apple Developer ID ($99/yr)
- [ ] Generate signing cert + App Store Connect API key
- [ ] Store secrets in GitHub: `APPLE_SIGNING_IDENTITY`, `APPLE_API_KEY`, `APPLE_API_ISSUER`, `APPLE_PROVIDER_SHORT_NAME`
- [ ] Enable signing in `tauri-apps/tauri-action@v0.6` step
- [ ] **Post-build DMG re-notarize step** (workaround for Sequoia):
  - [ ] `xcrun notarytool submit "$DMG" --wait --apple-id "$APPLE_ID" --password "$APP_SPECIFIC_PASSWORD"`
  - [ ] `xcrun stapler staple "$DMG"`
  - [ ] Re-upload stapled DMG as release asset
- [ ] Verify on clean Mac: no Gatekeeper warning on first open

---

## Phase F — CI release matrix (parallel track)

- [x] **`.github/workflows/release.yml`** — unsigned matrix build
  - [x] `macos-14` arm64 primary
  - [x] Intel / Windows / Linux stubbed (commented), ready to un-comment
  - [x] `workflow_dispatch` uploads as workflow artifacts
  - [x] Tag push (`v*`) attaches to GitHub Release
- [ ] First green run — push a `v0.1.0-preview` tag, see DMG attached
- [ ] Un-comment `macos-13` row once arm64 is stable
- [ ] Un-comment `windows-2022` row + add Windows-specific PyInstaller notes
- [ ] Un-comment `ubuntu-22.04` row once the Linux packaging path is chosen (AppImage vs deb)

---

## Cross-platform expansion (post-Phase-D)

One session per platform. Ordered by payoff:

- [ ] **macOS Intel (`macos-13`)** — easiest; same cert, different PyInstaller wheel set
- [ ] **Windows x64 CPU (`windows-2022`)** — `mlx_whisper` absent, fall back to PyTorch Whisper. Needs Windows code-signing cert (~$100–300/yr)
- [ ] **Windows x64 CUDA** — follow `scripts/package_cuda.py` pattern, ship as lazy-download pack
- [ ] **Linux x64 AppImage** — `appimagetool` builds; unsigned is acceptable on Linux

---

## Risks we're tracking

| # | Risk | Mitigation | Status |
|---|---|---|---|
| 1 | PyInstaller can't bundle torch Metal libs | Use `collect_all()` calls; fallback: portable-venv inside `.app/Contents/Resources/` | Untested |
| 2 | `torch.compile` breaks under frozen imports | Port `pyi_rth_torch_compiler_disable.py` hook | Pending |
| 3 | First-run model download fails halfway | SSE retry + resumable `hf_hub_download` (native) | Partial — frontend UI TBD |
| 4 | User has <10 GB free disk | `/setup/status` refuses download; shows error | ✅ Implemented |
| 5 | Gatekeeper blocks unsigned app | Document right-click → Open in README | Not yet documented |
| 6 | DMG size >800 MB | Exclude nvidia/triton/matplotlib; lazy-download optional packs | Exclude list in spec |
| 7 | macOS 15 Sequoia rejects un-notarized DMG wrapper | Phase E includes explicit `stapler staple` step | Blocked on Phase E |

---

## Change log (this doc)

- **2026-04-21** — initial tracker created. Phase C partially shipped:
  tqdm monkey-patch + `/setup/status` + SSE stream + `/setup/warmup` live
  and smoke-tested. Phase F skeleton committed (CI workflow, primary target
  only — non-arm64 rows parked). Phases A, B, D, E pending.
