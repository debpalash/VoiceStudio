# Electron and Tauri behavior parity

This inventory tracks user-visible behavior. A row is **implemented** when Electron exposes the
same task and backend contract. Platform-specific performance may differ. A native row stays
**verification pending** until its real macOS, Windows, and Linux smoke gates pass.

| Area | Electron state | Evidence | Remaining gate |
|---|---|---|---|
| First run and managed backend | Implemented | `docs/electron-runtime.md`, installed Debian/AppImage consent smokes, and fully uncached packaged Windows and Ubuntu 26.04 install-to-paused-first-sound runs | Full packaged uncached install-to-first-sound on macOS |
| Backend crash recovery | Implemented | crash/health smokes plus packaged Windows deliberate quit during deferred startup | Signed-package OS matrix |
| Voice cloning and saved profiles | Implemented | live Faster-Whisper uploaded-reference transcription plus atomic audio/transcript/portrait profile save, clone demo and profile image tests | Real engine smoke on each accelerator family |
| Voice design | Implemented | `docs/electron-voice-design.md`, design and persona smoke tests | Real model generation matrix |
| Stories and Audiobook | Implemented | `docs/electron-longform.md`, a real two-profile Stories cast/render/playback, real chaptered M4B render/playback, longform smokes, and packaged Linux native Save As | Native export smoke on macOS |
| Dubbing and batch dubbing | Implemented | `docs/electron-dubbing.md`, `docs/electron-batch.md`, live public URL/caption ingest, a 15-minute 195-segment Windows project, real interactive/batch model-backed exports, opt-in live-as-you-edit CAST playback, the shared bounded Agent Fit loop, and packaged timing-budgeted translation through Codex, Claude Code and OpenCode while Pi retains contract coverage and ordinary providers remain available | Repeat the long-media run on native macOS/Linux |
| Profiles and Gallery | Implemented | `docs/electron-gallery.md`, gallery and persona round-trip smokes, explicit no-key five-result portrait search, packaged Ubuntu native persona export/inspection, plus live Community outage/retry/favorite/preview/Designer handoff | None |
| Transcription and dictation | Implemented | `docs/electron-transcriptions.md`, transcription and native dictation smoke tests, plus trusted-origin audio-only Chromium permission regressions | Native hotkey/input permission matrix |
| Projects and Tools | Implemented | `docs/electron-projects.md`, `docs/electron-tools.md`, project and tool smoke tests, plus real Windows Faster-Whisper/OmniVoice conversion and Vidstack playback | Native reveal/export matrix |
| Model catalogue and engine selection | Implemented | model-library component tests, recommendations smoke, live install/cancel/delete/unload verification, current in-process/isolated Faster-Whisper selection and restoration, and repeatable app-managed audio.cpp v0.7.4 Sortformer inference/cancellation on Windows Vulkan | Native install matrix across model families and desktop OSes |
| Local and remote compute target | Implemented | worker API tests, compute-target UI, remote-target recommendation smoke, Ubuntu 26.04 WSL real TTS plus disconnect/reconnect fallback | Native-machine remote run outside WSL |
| Settings, logs, API reference and support | Implemented | settings smoke tests, responsive live runtime/hardware identity, 640 px layout smoke, packaged Linux 150% DPR/no-overflow visual smoke and `docs/electron-logs.md` | Native visual capture on macOS |
| Updates, notifications, app/tray/taskbar branding | Implemented; published-update verification pending | updater IPC lifecycle/authorization regression, platform-specific feed names, bounded channel-aware release history, byte/SHA-512 release contract, release-matrix validation of each unpacked Electron app, branded Windows NSIS/app icon, launched AppImage and installed Debian package with shared brand assets/native helper | First signed Stable/Preview update and installed-shell visual check on macOS/Linux |
| Watch folders and global capture | Verification pending | Windows native watch lifecycle/global shortcut/insertion plus Linux AppImage Wayland tray/watch and clipboard-fallback smokes | macOS permissions, Linux portal global shortcut and physical microphone matrix |
| T3-inspired shell, themes, scale and keyboard navigation | Implemented | shared resizable secondary panes, sidebar, locale-layout, workspace-shell and all-route accessibility smoke tests, including 640–1920 px secondary-pane geometry, 38 routes at 1440/640 px, packaged Linux 150% DPR and main-process empty-renderer recovery with a localized asset-independent fallback | Native screen-reader pass on macOS/Linux and high-DPI pass on macOS |
| In-app agent repair dock | Electron extension | `docs/electron-repair.md`, native smoke, packaged Codex/Claude/OpenCode acceptance on Windows, and packaged Linux host-shim rejection | Pi acceptance when installed; native macOS/Linux CLI matrix |

Latest local verification (2026-09-14): the final unpacked Windows build launched against its
managed runtime and reported backend 0.5.2 healthy on an RTX 4090. Its responsive System Preflight
resolved the live CPU, GPU/VRAM, Python runtime, compute device, selected engines and storage paths.
OmniVoice was resident on CUDA,
all required setup models were ready, seven saved profiles loaded, and Parakeet TDT v3 was installed
and active for dictation. A saved 17-second clone reference also completed a live Parakeet
transcription in 0.66 seconds with the expected text. Focused model/runtime/profile/transcription coverage passed 41 renderer
checks plus 150 backend checks with `HF_HUB_OFFLINE=1` and an empty Hugging Face cache. The shared
secondary-sidebar smoke also passed every workspace from 640 to 1920 px.
The complete Electron component suite passes 462 checks across 119 files; first-run browser
acceptance additionally covers partial preflight recovery, recommended-model disclosure and the
compact Settings layout, while all 38 routes fit at 1440 and 640 px and 37 routes pass the DOM
accessibility audit at both widths.
The refreshed packaged backend also reported the resident `k2-fsa/OmniVoice` checkpoint with a
non-empty UTC load timestamp, `cuda:0` execution and 1937.2 MB allocated VRAM, closing the anonymous
ready-state gap across renderer refreshes.
The current production renderer additionally switched the live ASR backend out and back without
losing its selected model, completed a real Faster-Whisper upload through timed segments, export,
Clone handoff and cleanup, and round-tripped a generated take into a disposable saved profile.
Its live route sweep remained free of renderer warnings, exceptions and API failures after
updater, dictation, QR, locale loading, translation-file and local-agent discovery rejection paths
were contained at their initiating UI boundaries.
The inventory guard maps every current Tauri page, every static Electron route and every model
family to its maintained capability or responsive-layout evidence; adding an unmapped page or route
now fails the required test gate.
An isolated clean Ubuntu 26.04 workspace also completed the Electron typecheck and production
package, then passed the unpacked Linux artifact contract with its x64 app, bundled `uv`, branded
resources and native dictation helper present.

The inventory records behavior and verification only. It does not reopen layouts or mechanics that
already satisfy their task. New parity work should update the relevant row and add the narrowest
regression evidence that proves it.

Electron also consumes the backend's `/ws/events` invalidation stream for profiles, generation and
dub history, projects, exports and model state. Local development waits for backend health before
opening the proxied socket; packaged and authenticated remote connections use the main-owned,
path-bound WebSocket URL and reconnect without exposing credentials.

`bun run smoke:live-routes` from `electron/` exercises all 37 concrete route views through the
running hash router and real backend. It fails on an error boundary, missing heading, uncaught
exception, console error or warning, failed load or HTTP error response. The 2026-09-13 live run passed every
route with no failures.

## Settings navigation

The main workspace sidebar stays visible on Settings routes, including while the backend is starting or unavailable. Settings keeps its own section list beside it, with a Settings heading instead of a duplicate brand header. Narrow windows use the same compact-rail behavior as other secondary-pane workspaces.

## Navigation order

In the expanded sidebar, workspace navigation sits immediately below the header and above the voice library. The status bar and Settings entry remain at the bottom; voice-library scrolling does not move the navigation.

## Sidebar expansion preference

Settings → Appearance → Keep sidebar expanded persists the choice locally. It disables automatic compaction on narrower secondary-pane workspaces; the default remains automatic. The explicit close control still works, and the Profiles workspace still avoids duplicating its voice library. The header toggle and sidebar share one state. A compact rail can reopen itself on every desktop platform, using the brand icon on Windows/Linux.
