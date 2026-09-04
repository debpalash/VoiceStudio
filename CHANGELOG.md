# Changelog

All notable changes to VoiceStudio.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
`frontend/package.json` is the app-version source of truth; Cargo, Python, and
the frozen-backend fallback mirror it for their toolchains.

## [Unreleased]

**Highlights**

### Changed

### Added

### Docs

### Fixed


## [0.5.2] — 2026-09-02

**Highlights**

- Show estimated and measured model, dependency, cache, and temporary disk costs in the engine catalogue (#1718)
- Preview builds now stay newer than Stable even when automatic post-release version bumps are disabled (#1762)
- CosyVoice setup guidance now separates downloaded model files from the runtime that makes the engine available (#1761)
- MCP tools can now keep audio out of agent context by returning files and accepting base-path-confined file inputs (#1760) — thanks @agudmund!
- Hear a dub line as you type it — an opt-in live preview streams TTS for the edited segment (#1769) — thanks @mvanhorn!
- Studio gains a Convert method: re-say any clip in one of your saved voices, speech to speech, fully local (#1765) — thanks @mvanhorn!
- Hardsub video export gains an opt-in karaoke word-highlight caption style (#1764) — thanks @mvanhorn!
- The batch queue can now watch a folder: new videos dropped into it are dubbed automatically (#1768) — thanks @mvanhorn!
- The audiobook player now shows the chapter text and highlights the word being narrated (#1766) — thanks @mvanhorn!
- The dub editor gains a casting board: drag voice chips onto speakers, dropdowns stay in sync (#1767) — thanks @mvanhorn!

### Changed

- Voice Design simplified: the 12-row fine-grained block collapses to one summary line with a five-field editor, English accent and Chinese dialect merge into a single field, and the starting-point chips now show 5 with an overflow toggle (#1793)

### Added

- The audiobook result is now a synced-lyrics player: chapter text follows playback with the current word highlighted and click-to-seek, timed from the render's own chapter durations with a karaoke-style even split — no ASR pass, fully local (#1766) — thanks @mvanhorn!
- The dub CAST strip expands into a project-level casting board: drag voice chips (clone profiles, design presets, Default) onto speaker rows — or pick from a keyboard listbox — writing the same per-speaker cast fields as the existing dropdowns (#1767) — thanks @mvanhorn!
- Studio's new Convert method turns a dropped or recorded clip into an existing voice profile's voice, with optional source-duration matching (#1765) — thanks @mvanhorn!
- Opt-in watch folder on the batch queue: pick a directory once and new videos are auto-enqueued with your last Add-to-queue settings, with pause/stop controls and copy-in-progress protection — files upload as bytes, paths never leave the app (#1768) — thanks @mvanhorn!
- Hardsub export can now burn karaoke word-highlight captions: an opt-in Line | Karaoke control renders a word-timed ASS sweep from timings persisted at transcription, with an even-split fallback for older jobs and translated tracks, plus a `GET /dub/ass/{job_id}` sidecar (#1764) — thanks @mvanhorn!
- Windows releases now include an independently updatable per-user MSI that installs and uninstalls without elevation (#1713)
- Dub segments can now stream live TTS while you edit a translated line — opt-in toggle, existing `/ws/tts` socket, shared generation admission, exports still render at full quality (#1769) — thanks @mvanhorn!
- Engine status and diagnostic bundles now record loaded execution provider, device, precision, fallback stage, accelerator identity, runtime versions, and parent-process memory visibility (#1717)

### Docs

- Local gigastt is now documented as a supported OpenAI-compatible ASR endpoint, with loopback privacy distinguished from remote servers (#1736) — thanks @ekhodzitsky!
- The CosyVoice guide now states that packaged builds have no one-click runtime installer and records the exact readiness checks exposed by [Discussion 1631](https://github.com/debpalash/VoiceStudio/discussions/1631) (#1761)
- A production private-API guide now covers pinned containers, root credentials, network isolation, streaming proxies, health checks, upgrades, and benchmark evidence (#1720)
- RX 6700 XT/gfx1031 over WSL2 ROCDXG is now explicitly unverified until a published end-to-end GPU workload proves the mapped path (#1716)

### Fixed

- The generation compute-time budget is now a Settings control (Performance & Device) instead of an env-var-only setting the timeout error recommended with no UI path — the error copy points there too, and long CPU/MPS renders get an upfront heads-up before they start (#1787)
- Windows: the backend can now start when the install path contains non-English characters (e.g. a CJK username) on a non-UTF-8 system code page — a new or broken Python environment now builds at an ASCII-safe path automatically (a healthy existing one is never relocated), and a specific error message names the cause and a working fix if the interpreter still crashes in `site` (#1783)
- Exports and other native-picker actions no longer 403 with "Invalid or expired desktop authorization" when the desktop app and backend resolve different data directories, e.g. dev mode or a custom data folder (#1781)
- Voice Design no longer lets you pick a Chinese dialect and an English accent together — the picker keeps them mutually exclusive instead of round-tripping a 400 (#1771)
- The desktop app no longer attaches to an already-running backend on version string alone: it now verifies the backend's actual code fingerprint too, so an orphaned or manually started backend reporting the current version but running older code (e.g. a stale `destination_path` export 422) gets replaced instead of adopted (#1770)
- Korean locale overhauled: 231 mistranslations corrected and all 493 missing keys translated (#1776) — thanks @j30231!
- Japanese "Cleaning…" clone status now reads as denoising instead of housekeeping (#1775) — thanks @j30231!
- The batch dubbing queue now has a UI entry point — a quiet link on the Dub landing (it was previously unreachable: the app switched on a mode nothing ever set) (#1768) — thanks @mvanhorn!
- OpenAI-compatible ASR now requires HTTPS outside loopback and refuses redirects so audio stays on the configured origin (#1736)
- Windows isolated engines now retain direct Job ownership without an extra Python supervisor process that can deadlock the child loader (#1734)
- The setup splash now waits through the backend's full startup budget instead of reporting slow Windows CUDA initialization as stuck after two minutes (#1749)
- Dubbing jobs can now reuse every source-language code produced by automatic ASR detection without a 400 error on the next upload (#1737)
- Incomplete Sherpa-ONNX model snapshots now self-repair before recognizer startup instead of failing on a missing ONNX file (#1733)
- OmniVoice subprocess startup now allows slow packaged Windows Python runtimes to signal readiness before termination (#1711)
- SRT files selected during source analysis now wait for speaker cloning, then replace transcript text without losing voices (#1709)
- Windows MSI deployments can now prohibit WebView2 bootstrap with `DISABLEWEBVIEW2BOOTSTRAP=1`, and `AUTOLAUNCHAPP=0` reliably suppresses first launch (#1714)
- Subtitle rows now provide 100 ms timing steppers and flag adjacent overlaps without requiring precise timeline dragging (#1710)
- Repair-sync failures now retain uv's final dependency error instead of reporting only an opaque exit status (#1705)
- YouTube ingest now retries yt-dlp's transient “page needs to be reloaded” response (#1706)
- Dictation model readiness now follows the live Hugging Face cache selected in Settings (#1707)
- Dictation capture now queues native events whenever its webview listener unmounts or reloads instead of emitting them to nobody (#1707)
- Desktop-contained backends now exit when their owning app disappears instead of surviving as stale port-3900 processes (#1707)

## [0.5.1] — 2026-08-28

**Highlights**

- OmniVoice generation on Apple Silicon now runs in a crash-isolated child, so fatal MPS memory exits no longer take down the local backend (#1697, #1698) — thanks @ndntran14!
- Model-load GPU exhaustion now returns a sanitized, actionable dubbing error, and readiness correctly attributes the shared model status to TTS (#1695)
- Source-mode development now restarts an isolated backend crash without tearing down the UI, while repeated crash loops still stop loudly with diagnostics (#1690)
- Dubbing playback now keeps an audible companion source when a WebView can render the preview picture but cannot decode its audio (#1692)
- Model Catalogue engine rows now use the available desktop width and keep identity, runtime state, and actions from crowding one another (#1689)
- VoiceStudio now acts as a local speech platform: other apps can trigger its native dictation or connect through versioned HTTP, WebSocket, JSON-RPC, CLI, and MCP transports (#1646)
- A timed-out in-process dub transcription no longer starts a second WhisperX/CTranslate2 call over the abandoned native worker, preventing the overlapping access that preceded Windows `0xC0000005` exits (#1669)
- Windows debugger termination code `0x40010004` is no longer misreported as a backend crash or charged against automatic restart recovery (#1663)
- Studio now keeps one generation reservation across page changes, preventing a remount from stacking native jobs until the backend reports capacity busy or is killed under memory pressure (#1670)
- Uploaded dubbing videos are normalized to browser-safe H.264/AAC before preview, preventing valid VP9, AV1, or Opus media from failing with “no supported sources” (#1644)
- Dubbing now separates spoken and target languages, preserves translations through segment cleanup, and lets failed translations be retried or skipped without restarting the batch (#1654) — thanks @Number16BusShelter!
- Importing replacement SRT subtitles now keeps each cue bound to the best-overlapping source speaker and clone instead of resetting every line to a random default voice (#1660) — thanks @invio-a11y!
- Uploading a Dub preview no longer blocks every backend request while ffmpeg extracts its audio (#1667) — thanks @tfreyd!
- Docker quick starts now require the administrator key needed through container NAT instead of starting a UI whose protected actions return 403 (#1651) — thanks @wd357dui!
- WSL2 AMD containers now use the `/dev/dxg` ROCDXG bridge with actionable GPU diagnostics instead of silently falling back to CPU (#1655) — thanks @wd357dui!
- Ad-hoc voice-clone references now stay alive until cancelled or timed-out GPU work actually stops reading them, so prompt caching can finish instead of failing on a deleted temp file (#1668) — thanks @tfreyd!
- Dictation now stays bound to the app where it started and recovers locally from silent recognizer output (#1175)
- The backend now answers within a second of launch and narrates its startup step by step (#1550)
- Reporting a bug from an outdated build now offers the latest release first (#1547)
- The backend is only announced ready once it can actually serve, and crash-loop restarts now pace themselves (#1548)
- Invisible watermarking no longer stalls — or silently skips — the first take of a session (#1615)
- Dub subtitles can be retimed, inserted, and merged in either direction from the segment table (#1612) — thanks @invio-a11y!

### Changed
- Model Catalogue now uses one breathable workspace canvas with simpler pane and engine-family navigation instead of nested cards and scroll regions (#1685)
- Linux source launchers now catch missing libxdo and GStreamer audio plugins before they can cause a linker error or an aborted, blank WebKit renderer (#1680, #1682)
- Dictation now carries one native output session from shortcut-down through final delivery, restores text, HTML, image, or file-list clipboards only when untouched, keeps Wayland copy-safe unless current-focus insertion is explicitly enabled, and retries silent Sherpa speech only through an already-installed local ASR model (#1175)
- The backend binds its port immediately and reports startup progress live — `/health` answers 503-with-step and a new `/startup/progress` endpoint lists every step while PyTorch, API routes, and database migrations load in the background, so "starting at step X" is never mistakable for "dead"; the desktop splash narrates each step (#1550)

### Added
- A bundled Rust loopback sidecar exposes dictation start/stop/toggle, focused-output sessions, discovery, and JSON-RPC; the backend adds versioned streaming events and a dependency-free CLI bridge for Herdr, coding agents, editors, desktop apps, and TUIs (#1646)
- Headless NVIDIA and ROCm machines can now join as worker-only Docker Compose services with no published UI and durable protocol-v2 enrollment; update both machines together before reconnecting (#1638) — thanks @jkrogers9862!
- Linux ARM64 (Asahi Apple Silicon) support for the OmniVoice GGUF engine — a `linux-aarch64` binary built with GGML Vulkan where the toolchain allows it, so Apple GPUs accelerate generation through the open-source Honeykrisp driver instead of falling back to CPU-only (#1641)
- One-command install on every desktop OS: `curl -fsSL https://voicestudio.sh/install | sh` (macOS/Linux/WSL) or `irm https://voicestudio.sh/install | iex` (Windows) — the URL serves the right script per platform, and Windows gains a source installer (`scripts/install.ps1`) with a 3-OS CI smoke (#1626)
- Per-line subtitle management in the dub table: a line's end time is editable alongside its start (typing a time and dragging its timeline edge now take the same path), lines merge with the previous row as well as the next (`Ctrl/Cmd+Shift+M`), and a new line can be inserted into the gap after any row (#1612) — thanks @invio-a11y!
- CI now enforces performance regression budgets on the hot paths — operation-count tests pin streaming TTS to one synthesis per sentence and cached dub re-mixes to zero re-synthesis; fast-path guards cover zero re-decoding and ⌈N/W⌉ native batch calls when enabled (#1594)
- Default-engine dubbing now synthesizes several segments per forward pass instead of one call per line — the width follows the host's device headroom (1 on CPU and low-VRAM cards, up to 8), `OMNIVOICE_DUB_BATCH_WIDTH` overrides it, and engines without native batching keep the single-segment path (#1594)
- `/ws/tts` now reports real time-to-first-audio, and its RTF measures synthesis alone so a slow client can't inflate it (#1594)
- The locally cached AudioSeal watermark generator warms on a background thread ~35s after boot (`OMNIVOICE_PRELOAD_WATERMARK=0` opts out; explicitly setting `=1` may download it), so the first synthesis no longer serializes the audioseal import + model load inline — measured at ~42s on a cold filesystem, 3s short of a 90s client timeout (#1576) — thanks @paoloantinori!
- Voices you've cloned stay "warm" across restarts — encoded references now persist to disk (~10 KB each), so the first generation of a session skips the re-encode and any transcription pass; `OMNIVOICE_PROMPT_DISK_CACHE=0` opts out (#1565)
- Optional FlashInfer acceleration for the default engine on CUDA (`OMNIVOICE_FLASHINFER=1`, ~2.2x measured) — needs the optional `flashinfer-python` package; missing package or kernel failure logs why and falls back to the standard path (#1565)
- The bug reporter notices when you're on an outdated build and offers the latest release before filing — with a "File anyway" escape hatch — and stamps a `Build status` line into every report so up-to-date reports are tellable from stale ones (#1547)
- Settings → Performance & Device gains a compute-device override (Auto / CUDA / ROCm / XPU / MPS / CPU, or `OMNIVOICE_DEVICE`) — pin the device when auto-detect picks wrong; only devices your machine actually has are offered (#1557)
- Opt-in 24-layer PocketTTS checkpoints via `OMNIVOICE_POCKETTTS_24L` — better prosody for it/de/es/pt at roughly 2x render time (still faster than real-time); the fast 6-layer model stays the default (#1613) — thanks @paoloantinori!

### Docs
- Supported-version and install guidance now identifies 0.5.1 as the stable desktop and container release (#1687)
- The Docker Hub overview now shows the current engine-switching demo, Model Catalogue, and gallery voice workflow (#1593)
- The Docker Hub overview and install guide now show the v0.5 tags and the built-in API-key/share-PIN security model instead of obsolete v0.4 and no-authentication guidance (#1592)
- The READMEs now lead with download buttons and a three-step first-clone walkthrough, and a new benchmarks page anchors measured per-engine/per-device numbers on the in-repo harness (#1555)
- Every engine now has its own guide — 21 new pages under docs/engines plus an index covering all 16 TTS and 11 ASR engines, linked from both READMEs (#1556)
- The OmniVoice guide now covers combining style attributes with a reference clip (consistent instruct stabilizes cloning; the reference wins conflicts), inline pronunciation control (pinyin / CMU phonemes), and corrects the claim that the default engine can't do voice design — it can, from attributes (#1565)

### Fixed
- Workspaces now measure their responsive width when the post-bootstrap shell actually mounts, so native UI scaling reflows Projects and History instead of crushing the Dubbing demo into unreadable columns (#1683)
- Dubbing keeps the source-language selector visible after a local file is chosen, so ASR can be pinned before transcription starts (#1678) — thanks @Lonki-lomki-cloud!
- First-run media-engine downloads become available to TTS immediately without a restart, and missing media-process failures now point to repair controls (#1677) — thanks @farhataligpt-dev!
- Source installs on AMD GPUs honour `OMNIVOICE_TORCH_VARIANT=rocm`: `bun run desktop` now swaps in the ROCm torch wheel after `uv sync` and launches the backend without re-syncing, instead of silently reverting to the CPU-only CUDA build on every start (#1665) — thanks @uberclokr!
- `bun run desktop` on a fresh clone no longer fails with "resource path `../../frontend/dist` doesn't exist" — the dev launcher creates the placeholder Tauri resource directory before compiling (#1664) — thanks @uberclokr!
- macOS no longer loses TTS after the first request when Python lacks `os.waitid`; subprocess ownership now uses a safe `waitpid` fallback without risking reused process groups (#1656) — thanks @paoloantinori!
- Desktop startup, Retry, reset, uninstall, shutdown, and crash recovery now share one backend lifecycle owner; quitting interrupts first-run installers and gracefully drains then force-cleans the full backend process tree, so overlaps cannot duplicate or orphan it (#1635) — thanks @Xohaibxobi!
- Large Stories and Audiobook projects now persist in IndexedDB instead of overflowing the `omnivoice.app` localStorage envelope, with quota-safe migration and orderly exit/reload flushing (#1636) — thanks @leodzai!
- OmniVoice and its crash-isolated subprocess now route to AMD ROCm GPUs instead of warning and falling back to CPU (#1629) — thanks @j4r3kb!
- Dictation now cancels pending startup work, capture resources, sockets, and timers when the capture widget closes, preventing late work against a destroyed webview (#1645)
- Streaming generation failures now show recognized recovery guidance and appear in Diagnostics instead of only returning a generic error (#1607)
- The worker-capacity transport test no longer races its own setup: the 1-slot limit now goes through the enrollment handshake instead of mutating client config after connect, where the server's stream-open ConfigUpdate (carrying the registered capacity of 2) could overwrite it and fake an over-accept; failed CI twice on 2026-08-21 (#1630)
- Moving words across a speaker boundary in a dub — merging two lines and splitting them again — no longer dubs the second half in the first speaker's voice; each half now keeps the speaker, voice, direction, gain, and language of whoever actually says it (#1612) — thanks @invio-a11y!
- Dictation on a WebView that refuses a 16 kHz audio context (WKWebView) now low-passes before downsampling, so frequencies above 8 kHz stop folding into the speech the recognizer is fed (#1610)
- A microphone context that cannot be resumed now reports a mic error instead of leaving the dictation pill on "Listening" while capturing nothing (#1610)
- Dictation no longer retains a whole session's audio for silent-model recovery — an open mic grew that buffer by ~115 MB an hour; the recent two minutes are kept instead (#1610)
- The clipboard-delivery status is now translated in all 21 languages, so Wayland users — where clipboard delivery is the default — no longer see an English string (#1610)
- A native sherpa-onnx load failure of any exception type now degrades to "engine unavailable" instead of taking the dictation WebSocket down (#1610)
- Dictation now ships Whisper Tiny as its one cross-platform default, avoiding Parakeet's measured empty decoding on Windows while keeping Parakeet selectable behind runtime fallback (#1175)
- Re-mixing a dub no longer decodes, rewrites, and re-reads every cached segment — same-rate cached audio is reused directly (and rejected if truncated), switching timing modes can't reuse slot-truncated audio as natural-rate, and RVC respects natural-rate modes (#1594)
- PocketTTS French works again — pocket-tts only ships a 24-layer French model and rejected the name the sidecar asked for, so every French request failed at model load; French now always loads `french_24l` (#1613) — thanks @paoloantinori!
- Installing IndexTTS 2.5 no longer fails claiming an interrupted download — the weights repo ships `config.yaml` and VoiceStudio demanded a `config_v2_5.yaml` that exists in no upstream release; both names are accepted, so a hand-renamed checkout keeps working (#1611) — thanks @zuiaiyutu!
- IndexTTS 2.5 no longer has long-text generation killed at 60 seconds — the sidecar now proves it is alive every 5 seconds while `infer()` runs, and its deadline rises to 900s (`OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S`) (#1611) — thanks @zuiaiyutu!
- The OpenAI-compatible `/v1/audio/speech` route now reuses the shared cached engine for explicit `model` ids instead of constructing a fresh engine — and its sidecar/model load, a ~28s floor per call for subprocess engines — on every request, with the same single-engine-resident discipline `/generate` applies (#1614) — thanks @paoloantinori!
- The setup wizard's RAM check no longer blocks 8 GB machines whose OS reports ~7.8 GB usable — the thresholds now tolerate reserved memory, and `OMNIVOICE_RAM_PREFLIGHT=0` turns a genuine block into a warning for those who accept the OOM risk (#1618)
- Invisible watermarking now runs eagerly instead of through `torch.compile` — AudioSeal's lazy compile sent the first embed of every session into Inductor's C++ codegen, which failed outright on macOS hosts whose toolchain couldn't serve it and shipped the audio unmarked after a 30-40s wait; first embed drops from 9.70s to 0.26s (#1615) — thanks @paoloantinori!
- The macOS Accessibility blocker now rechecks while visible and closes as soon as the grant is enabled instead of keeping a stale permission prompt on screen (#1609)
- The dubbing editor's video and transcript columns can now be resized by pointer or keyboard, and the chosen split persists across launches (#1571) — thanks @invio-a11y!
- CPU-only synthesis now gets a bounded ten-minute execution budget, and a render that exhausts it is reported as a compute timeout instead of misleading "generation capacity is busy" queue pressure (#1588) — thanks @ChienNguyen1111!
- Rapid Launchpad ↔ Dub navigation now replaces the workspace DOM owner cleanly, so late media/waveform cleanup cannot trigger React's `insertBefore` crash (#1590) — thanks @nicolas-jacques!
- Watermark embedding failures now log the full traceback instead of just the exception message, so a silently-unmarked-audio incident (audio passes through unmarked by design) is diagnosable from the log alone (#1576) — thanks @paoloantinori!
- Dubbing now recovers rapid two-speaker exchanges when diarization collapses them, defaults new projects to lip sync without overwriting saved timing choices, and keeps the editor usable on narrow screens (#1584) — thanks @victordonat0!
- `OMNIVOICE_ASR_BACKEND=omnivoice` now selects the PyTorch-native Whisper path, so the documented ROCm escape hatch no longer fails as an unknown engine (#1582) — thanks @patmansk!
- Network Sharing from Windows MSI/portable installs now serves the bundled web interface to LAN devices instead of redirecting them to their own `localhost` (#1589) — thanks @TWIISTED-STUDIOS!
- Exported dubbed videos now mark the dubbed language as the default audio stream while keeping Original available as an explicit choice (#1575) — thanks @invio-a11y!
- Cloning references can no longer exhaust system memory: transcript-free clips up to 75 seconds are searched in five bounded passages, longer clips ask to be trimmed, and supplied transcripts remain capped at 20 seconds to preserve alignment (#1578) — thanks @ACKAPOB!
- Stored artifact subpaths now resolve after moving a data directory between Windows, macOS, Linux, and Docker, while traversal and symlink escapes remain blocked (#1559) — thanks @Eman-Yousaf!
- A remote browser hitting an API-key-configured server's admin 403 now gets the API-key login form instead of endless console 403s, while desktop and PIN-only/no-key servers keep the plain loopback error so guests are never offered a login no key can satisfy (#1568) — thanks @paoloantinori!
- The crash-isolated ASR sidecar and its download preflight now agree on which model to load — setting the shared faster-whisper model variable applies to both variants instead of the sidecar quietly using a different one (#1556)
- "Ready" now requires the deep health probe (a working database-backed route), not just the identity probe — a backend whose install broke underneath can no longer be announced up while every real request fails (#1548)
- Supervisor restarts after repeat crashes now back off (immediate, then 5s, then 15s) instead of respawning back-to-back, so a tight crash loop can't burn the whole restart budget in seconds (#1548)
- The Linux desktop cleanup regression test now isolates build artifacts, so an existing developer build can no longer change its result (#1566)

- Renaming, deleting, or revoking consent on a voice (and starring/clearing history, recording exports) now live-updates every open tab again — the sync routes' WebSocket events were silently dropped, which could look like "all my voices are gone" (#1561) — thanks @paoloantinori!

### CI
- Project agents now share pinned Vite and FastAPI skills from skills.sh (#1594)
- Weekly full-history secret scans no longer mistake the Ed25519 private-key type name for committed key material (#1591)

## [0.5.0] — 2026-08-13

**Highlights**

- The app is now **VoiceStudio** (previously OmniVoice-Studio) — one waveform-and-spark identity across the app, docs and installers. Your data folder, settings and Docker image paths stay put.
- **Model Catalogue** — engines and models in one workspace: every TTS, transcription and LLM engine with its device routing and install state, defaults picked there.
- Switch TTS, ASR and LLM engines from the status bar or any workspace — ready-only choices, memory status, environment-pin protection, `Ctrl/Cmd+E`. (#1530)
- Lend another machine's GPU with a join code and a QR scan — a Compute control in the status bar picks where jobs run, and several people can share one GPU box with revocable, certificate-pinned connections. (#1516, #1496)
- Server mode is locked down: admin actions require an API key (#1525), and the remote UI exchanges it for short-lived sessions that never sit in browser storage or WebSocket URLs (#1528) — thanks @bultodepapas!
- A faster, cleaner Dub workspace for multilingual production, with a production command bar and per-language cards. (#1489)
- The demo audio and video the app always advertised now actually ship, rendered by VoiceStudio's own engine. (#1517)
- Dictation works on Wayland now — the portal shortcut actually fires (#1490, #1526) — and the recording pill is back on every desktop.
- The Launchpad wears the project's signal-field waveform artwork over a quieter, borderless layout. (#1533)
- The catalogue reads as headroom, not breakage: available engines sort first, uninstalled ones say what they need (#1531), and the LLM row names the provider that actually answers (#1538).
- Gallery voices can be saved as local profiles — audio lands in your profile store with validated, content-addressed references. (#1542)

<img src="https://raw.githubusercontent.com/debpalash/VoiceStudio/main/docs/media/0.5.0/quick-switch.gif" alt="Switching TTS engines from the status bar" width="820" />

| The Model Catalogue | The Voice Gallery |
| --- | --- |
| <img src="https://raw.githubusercontent.com/debpalash/VoiceStudio/main/docs/media/0.5.0/catalogue.png" alt="Model Catalogue — engines pane" width="420" /> | <img src="https://raw.githubusercontent.com/debpalash/VoiceStudio/main/docs/media/0.5.0/gallery-save.png" alt="Voice Gallery — save a voice as a profile" width="420" /> |

### Changed

- Gallery personas now preview through the local backend, retain their complete voice-design recipe, and open directly in Voice, Stories, or Audiobook. (#1542)
- Typing and large workspace edits no longer serialize and rewrite persisted documents on every input; writes are coalesced off the interaction path — thanks @bultodepapas! (#1541)
- Support amount choices now use every theme's shared card, accent and focus tokens. (#1530)
- Sponsoring, commercial licensing and getting in touch are one page now. They answered the same question between them and each used to live somewhere else, so they are three sections on a single scroll — the footer heart, the commercial-licence links and Contact all land on it, at the section you asked for. (#1522)
- Model Catalogue switches panes with tabs instead of a two-state toggle, and the Engine Compatibility Matrix's TTS / ASR / LLM switcher is now tabs too — arrow-key navigable, and each tab still shows the engine it would use. (#1522)
- Engines you can actually use sort to the top of the compatibility matrix, and an unavailable engine's name recedes instead of the whole row fading — the status badge and GPU chips that say *why* it is unavailable stay legible. (#1522)
- Remote workers reads as a device list: status dot, address, latency, a live task meter, resident models and last-seen per machine, with housekeeping actions revealed on hover and a three-step empty state. (#1516)
- The GPU picker and the new status-bar control paint their status dots and menu surfaces from themed tokens instead of fixed palette classes, so they stop showing Gruvbox colours on Midnight and Catppuccin. (#1516)
- Dictation shows the pill again: a capture puts a small always-on-top capsule near the bottom of the screen you are working on — listening, transcribing, the result, and any error — and takes it away when the session ends. It never takes focus, so the text still lands in the app you were typing into. On Wayland the compositor decides where it sits; everywhere else it is bottom-centred.
- Engines and models moved out of Settings into a new Model Catalogue workspace, reachable from the icon rail (or the title-bar tabs); Settings → Engines and Settings → Models now point there, and Settings keeps the models directory and Hugging Face mirror.
- The Settings sidebar is keyboard-navigable: ⌘K / Ctrl+K jumps to the filter, ↑/↓ and Home/End move between categories, and Enter or ↓ from the filter drops into the list. Matching text in a filtered category name is highlighted, and group headers stay pinned while the list scrolls.
- The Launchpad has a quieter, more spacious look: borderless feature tiles that light up on hover or keyboard focus, plain-numeral counts, hairline section rules, and one shared page column for the hero, tiles, recent files and project lists.
- Linux release smoke now validates linuxdeploy's wrapped custom launcher instead of rejecting a healthy AppImage. (#1506)
- Remote GPU workers render audiobooks chapter by chapter, with automatic per-chapter local fallback and one combined notice if the worker drops out. (#1478)
- Remote GPU workers can now run a job to completion: long renders no longer die at two minutes, a worker that drops and reconnects mid-render keeps its work, and a timed-out job no longer takes the worker offline for good. Placing a job still needs the development-only `POST /workers/tasks`; wiring the app's own Synthesize button to it comes next.
- Voice, Stories, Audiobook, Gallery, Settings, profiles, and Launchpad now use compact, responsive layouts with accessible controls. (#1491)
- Dubbing's Generate Dub, Verify, and Export actions now use a compact hierarchy with visible labels, responsive reflow, and motion-safe feedback. (#1493)
- The Dub workspace now has a compact production command bar, responsive flag-based language cards, media previews in Dub History, and a narrower Projects rail. (#1489)
- VoiceStudio now uses one waveform-and-spark mark across the title bar, About screen, README, browser favicon, and every desktop/platform icon. (#1487)
- PocketTTS now asks you to review its code license, model license and gated-access conditions before first use, and explains how to unlock the model instead of showing a raw download failure — thanks @paoloantinori! (#1442)
- The repository moved to github.com/debpalash/VoiceStudio. Every link in the app, docs and scripts now points there; GitHub redirects the old URLs, and the Docker image paths, the app bundle identifier and your data folder are all deliberately unchanged. (#1394)
- The app is now **VoiceStudio** (previously OmniVoice-Studio). Only the name you see changes — your data folder, settings and the Docker image paths stay put, so upgrading needs nothing from you. On Linux the .deb is now `voicestudio`; remove the old `omnivoice-studio` package once.
- macOS floor raised to 13.3 (Ventura) — the frontend has required Safari 16.4 for some time, so macOS 12 was a promise the stack could not keep (#1268)
- The first-run setup screen no longer overpromises. It claimed "no account, no cloud, no telemetry" without qualification — untrue for anyone who opts into analytics — and now says what actually holds either way: your voices, recordings and projects never leave the machine, and no processing happens in the cloud.
- Dictation no longer shows a floating pill. The hotkey records, transcribes and pastes with nothing on screen; the tray icon still marks recording, and anything needing your attention (Accessibility, microphone, a failed transcription) now arrives as a notification in the main window.

### Added

- Gallery personas preview through the local backend, keep their full voice-design recipe, and open directly in Voice, Stories, or Audiobook — and can be saved as local profiles with validated audio references. (#1542)
- The demo audio the app has always advertised now actually ships: previews for all seven voice-design presets, the three dictation replay clips, and the dubbing demo's source video plus four dubbed languages with subtitles. Every one of those was a dead link before — the tooling that renders them required macOS, so on Windows and Linux the files were never built. (#1517)
- Demo assets are rendered by VoiceStudio's own engine, so the tooling runs wherever the app does, and the demos are made by the thing they demonstrate. (#1517)
- A machine can now join a control plane from the app: Settings → System → Remote workers → **Lend this machine's GPU**, paste the join code, done — no environment variables and no restart. The address travels with the code, so the machine reconnects on its own afterwards. (#1516)
- Join codes and connection strings are shown as a **QR code** alongside the text, with a live expiry countdown — scan it from the other machine instead of retyping forty characters. (#1516)
- A **Compute** control in the status bar: pick local or a remote machine, turn remote workers on or off, and mint a join code without opening Settings. It appears only once you have opted in or enrolled a machine. (#1516)
- A worker waiting for approval can be approved from its row. The panel labelled that state before but offered no way out of it. (#1516)
- **Model Catalogue** — a workspace of its own for engines and models: browse every TTS, transcription and LLM engine with its device routing and install state, pick the default for each, and install or remove model weights, all from one screen instead of two Settings categories.
- Remote GPU machines can now accept connections instead of dialling out, so several people can use the same box at once — each gets their own revocable connection string, with certificate-pinned TLS, a live list of who is connected, and a disconnect button. (#1496)
- Remote GPU model downloads now use the normal Models install flow and show per-worker progress. (#1478)
- Settings → System → **Remote workers** sends individual jobs to GPUs on your other machines while everything else stays here. Off by default; each machine is added with a single-use token and approved before any audio reaches it. See [docs/remote-workers.md](docs/remote-workers.md).
- First-run setup now recommends a screen-aware interface scale, with compact controls available throughout setup. (#1502)
- OrcaRouter is now available as a named OpenAI-compatible LLM provider — thanks @Marc-oss-hub! (#1499)
- IndexTTS 2.5 is available as a pinned one-click sidecar with five-language dubbing, expressive cloning, and backward-compatible IndexTTS-2 support. (#1482) — thanks @marwanlhabti5-coder!
- Voice recording now offers microphone and channel selection with a live input-level meter on every desktop platform. (#1481)
- Settings → Appearance → **Navigation style** switches the workspace switcher between the icon rail down the window edge and browser-style tabs across the title bar. Both offer the same workspaces; the choice sticks across launches, and the rail stays the default. Tab labels fold down to icons when the title bar runs out of room — the workspace you're in keeps its name. (#1412)
- Portable mode lets you choose the folder — press **Change…** on the first-run setup screen and put the whole install on an external drive. It also stops being greyed out after a default Program Files install. (#766)
- Settings → Privacy now has an **Invisible watermark** toggle. On by default, available to everyone, and it only affects audio generated after the change. (#1308)
- A new opt-in crash-isolated TTS engine, so a native crash takes down the sidecar instead of the whole backend — thanks @paoloantinori! (#1292, #1298, #1304)
- **PocketTTS** (Kyutai), an opt-in CPU-only engine for fast, low-latency renders in six languages (en/fr/de/pt/it/es) with zero-shot cloning from a reference clip. Enable in Settings → Engines — thanks @paoloantinori! (#1306, #1328)
- A warning before a slow generation, rather than after a five-minute wait. (#1280)

### Docs

- Engine acceptance: new `docs/engine-acceptance.md` documents the job map, the bar a new engine must clear, and the out-of-tree path (#1306)
- macOS install notes and the README support table now state the real floor (#1268)
- Contact: the project X account is listed alongside Discord (#1313)
- `OMNIVOICE_ALLOWED_ORIGINS` is finally documented: a browser loading the UI from another machine's origin needs the backend's CORS allow-list, which neither server mode nor trusted networks touches — thanks @vanderlpp! (#1348)

### Fixed

- AMD/ROCm hosts no longer crash ASR with "CUDA driver version is insufficient": ROCm torch reports itself as CUDA, but whisperx/faster-whisper run on CTranslate2, which is NVIDIA-only — they now take the CPU path there, and auto-detect prefers pytorch-whisper, which genuinely uses the HIP GPU. (#1529)
- Crash reports now carry the crashed run's own stderr: the shared error log is append-only with per-run offsets, so a restart can no longer overwrite the dying process's final output with the replacement's healthy startup. (#1510)
- Wayland: a stale portal identity no longer kills the dictation shortcut for the whole session. The desktop entry the app writes for the GlobalShortcuts portal could point at a binary that has since moved (a `cargo clean`, a relocated AppImage) — GNOME then refuses the bind with "App info not found" and the hotkey silently dies. The entry is validated and rewritten at startup now. (#1526)
- The guard that keeps transcription on the degrading ASR loader now scans the whole backend, not just the routers — a service that transcribes on a request's behalf skipped `ensure_loaded()` just as thoroughly. (#1519) — thanks @ahov520!
- The Linux app icon is no longer blank. Every AppImage since v0.4.2 shipped `.DirIcon` as an absolute symlink into the machine that built it (`/home/runner/work/…`), so the link dangled on every user's computer and file managers, app menus and desktop integration all drew nothing. The release build now verifies the icon resolves inside the bundle before publishing. (#1518)
- The Linux desktop entry no longer ships an empty `Categories=`, which `desktop-file-validate` rejects and menu builders skip. (#1518)
- Wayland: the dictation shortcut now actually starts dictation. The desktop portal registered the key correctly — GNOME and KDE even showed it back — but every press was discarded while decoding the compositor's signal, so the hotkey did nothing on any Wayland session. (#1490)
- The first-run "Choose a comfortable UI size" screen no longer stutters while you sit there. Applying a scale resizes the window's own viewport, which the screen was reading back to re-pick a size — so it flipped between two sizes forever without anyone touching it. (#1514)
- Transcription now moves to the next working engine when the auto-picked one passes its availability check but breaks on first real use, instead of returning an internal error — the recovery dubbing already had. Affected accurate-mode transcription, the OpenAI-compatible API, batch, dub verify, and voice-clone reference text. (#1512)
- A malformed request now gets a clear 422 instead of an internal error, and uploading a file to an endpoint that expects JSON no longer copies the whole upload into the app log — a 145 KB clip wrote roughly 500 KB of log, recording your audio in the file people paste into bug reports. (#1513)
- The Simplified Chinese (zh-CN) translation no longer mistranslates brand names and technical terms — Discord, Tailscale, Hugging Face, IPA, and LLM (Cinematic) were rendered as nonsensical literal translations, and ~250 more awkward machine-translation strings are now natural Chinese. (#1508) — thanks @anyingiit!
- Worker restart coverage now waits for the registration response to persist its identity instead of racing the client callback in CI. (#1505)
- Dub language and export selections now restore without false schema warnings, and remote-worker port 7443 is identified instead of reported as a generic timeout. (#1504)
- A configured remote backend now bypasses local first-run setup, verifies itself before app requests begin, and shows recovery instead of leaving the desktop stuck on Setup. (#1503)
- An idle voice model now actually hands its memory back. The unload emptied the GPU cache a moment before releasing the model, so it freed nothing while reporting success — a GPU machine lending its card sat on 3.6 GB indefinitely. (#1495)
- Unloading a model on an NVIDIA GPU now returns the last ~770 MB too. A single 8.5 MB cuBLAS workspace sat inside the model's memory block and kept the whole block reserved, so an idle machine held 1.2 GB instead of 470 MB no matter how often you pressed Flush Memory. (#1495)
- Flush Memory reports reserved GPU memory alongside allocated. Allocated alone reads near zero right after an unload while the GPU still shows gigabytes, which is exactly the case people were reporting. (#1495)
- The AudioSeal watermark models are released after the same idle period as everything else, instead of staying in memory for the life of the app once anything was watermarked. (#1495)
- Remote GPU workers now synthesize a dub's fresh segments as one coarse job with live progress and cancellation; fitting, assembly and RVC remain local. (#1478)
- Gallery voice previews now fall back to a local render when a downloaded clip cannot be decoded, instead of failing silently. (#1478)
- A second VoiceStudio instance can no longer silently share the remote-worker port; it keeps running locally and explains how to resolve the conflict. (#1478)
- Remote GPU jobs stay pinned to the selected worker across retries and restarts, stop when their caller leaves, and cannot return from cancellation as completed. (#1478)
- Remote GPU model labels now survive registration, legacy blank model IDs share one capacity slot, long jobs retain bounded leases, and idle cleanup cannot evict a live local render. (#1478)
- Remote GPU jobs now stop before dispatch when that worker lacks the model, offer the download there, and refresh scheduling as soon as it finishes. (#1478)
- Leaving a screen while its waveform is still loading no longer opens a bug-report prompt for a normal cancelled request. (#1498)
- An unreachable remote backend now opens a retryable recovery screen instead of sending the app into local model setup, with clear TLS, CORS, network, HTTP, and wrong-port guidance — thanks @debpalash! (#1501)
- Linux production test launches now stop their own extracted AppImage before resetting SQLite and logs. (#1494)
- Restored the pre-release version to 0.4.2 while the next release remains in preparation. (#1488)
- Large multi-language dubbing batches now use compact searchable language and track managers instead of overflowing the editor. (#1492)
- Dictation shortcuts now register and rebind through the desktop portal on Wayland, honor custom keys in focused app views, and show the effective platform keys. (#1490)
- Multi-language dubbing now translates, edits, generates, retains, and exports every selected language, and its language picker stays visible at viewport edges. (#1486)
- Dubbing's **From video** cast now uses available source-audio samples for every speaker and short line, including jobs without a pooled diarization clone. (#1484)
- Basic Dubbing translation remains available without an LLM; Cinematic and Autofit now degrade through the existing Fast translation path instead of blocking the quality choice. (#1481)
- Linux microphone recording now falls back to WAV when WebKit cannot encode MediaRecorder audio, and desktop scaling/titlebar controls remain responsive at every UI scale. (#1481)
- Dubbing can install a missing ASR model and retry the same job, navigate back through completed stages, and finish transcription under low GPU memory without producing an empty transcript. (#1481)
- Filenames and other outside data can no longer forge extra lines or terminal commands in backend and frontend diagnostic logs. (#1457)
- Backend journal, dictation reset, voice-catalog, and crash-notification failures are now visible and retryable instead of being silently ignored. (#1459)
- Backend failures keep raw tracebacks, local paths and credentials in the local log instead of returning them in API responses. (#1454)
- GPT-SoVITS connections now stay on loopback or explicitly trusted networks and cannot escape through redirects or DNS rebinding. (#1463)
- Engine discovery no longer exposes probe exceptions, local paths or credentials in API responses and logs. (#1460)
- Failed gallery, batch-video, and desktop-log cleanup is now reported instead of silently claiming success, and diagnostic redaction fails closed if a scrubber breaks. (#1458)
- Remote backends can no longer probe or overwrite arbitrary host files through native-only tools, and imported or persisted paths cannot escape their VoiceStudio data folders. (#1455)
- Linux releases now verify that the AppImage actually contains the compatibility launcher, instead of silently shipping Tauri's stock launcher and opening as a blank window on newer Mesa systems. (#1464)
- Patched dependency releases now cover 35 Python and Rust security advisories without weakening VoiceStudio's GPU or offline-runtime compatibility. (#1456, #1472, #1473, #1474, #1475, #1476, #1477)
- Curated models now install and repair from reviewed, immutable revisions; custom MOSS remote code requires an explicit safety opt-in. (#1453)
- YouTube imports that require a signed-in session can now use an explicitly selected `cookies.txt` export for one import; VoiceStudio never reads browser cookies silently and makes two best-effort attempts to delete its temporary copy. (#1429, #1432) — thanks @dongqing1968-sudo and @phamvandu9595-tech!
- First-run source builds no longer stop after uv was successfully downloaded just because its installer failed during a later shell-profile step; app-private uv installs no longer touch shell profiles at all. (#1438) — thanks @AdrianoCahete!
- Model files damaged by an interrupted download now repair themselves instead of failing every generation, including invalid `config.json` files and corrupt weight headers. — thanks @overrunau and @zherunh! (#1406, #1437)
- ROCm Docker now installs and starts the backend with the same Python whose AMD torch build was validated, instead of launching a second CUDA-only environment and silently running on CPU. (#1274) — thanks @simmessa and @spicchio72!
- An error whose text merely contained the digits 401 — a file path, a byte count, a job id — no longer tells you to fix your Hugging Face token. (#1427)
- Custom MLX model IDs and saved voice instructions are now validated in bounded time, so malformed input cannot stall the backend. (#1446)
- Streaming and provider failures now return stable recovery guidance without exposing exception details. (#1462)
- Server-mode settings mutations require the admin API key, while host destinations and executable paths can only be selected through the native desktop app. (#1448)
- Automatic model-mirror checks now reject untrusted URLs before opening a network connection. (#1447)
- Sidecar engines no longer break when a library they load prints to the console. Those bytes landed in the middle of the engine's data stream, failing the generation and leaving the connection scrambled for every request after it. (#1428) — thanks @1335-Group!
- A generation abandoned while stuck on an internal lock now says so, instead of blaming your hardware and suggesting shorter text. Nothing had been computed, so none of that advice applied. (#1416, #1419)
- A machine with a GPU that ends up on CPU now says why — a missing device node, a permissions problem, a card newer than the installed ROCm, an `HSA_OVERRIDE_GFX_VERSION` that is doing more harm than good, or an NVIDIA driver the container can't reach each read differently. Before, all of them looked identical to having no GPU at all. (#1274, #1228)
- The first generation on an engine that still has to install itself no longer gives up part-way. The install reports progress now, so the generation waits for it instead of hitting its own five-minute limit. (#1414)
- A slow machine is no longer told its IndexTTS-2 install isn't there. The check that confirms an engine's virtualenv gave up after 10 seconds and counted that as a broken install, so a cold first run 500'd; it now waits longer and treats slow as unproven, not broken. (#1414) — thanks @OracleNightmare!
- A broken Python environment now says so, instead of blaming the app's own install. A missing or mismatched torch/transformers surfaced as "omnivoice not importable" and sent people reinstalling the wrong thing. (#1415)
- A model that fails to load at startup no longer leaves the app looking healthy while producing nothing — the failure and its remedy now show up in the model status. (#1415)
- Generating with the default engine works again on everything built from `main` since the rename — source checkouts, preview builds and Docker `:latest` all run the same backend, whose model import had been rewritten to a class name the library doesn't export, failing every generation with "cannot import name 'VoiceStudio'". The class keeps its library name, and a guard test now pins it. (#1420)
- Running from source no longer dies at startup when a database migration is pending. Alembic resolved the migrations folder relative to wherever the app was launched from — fine from the repo root, fatal from the desktop shell (`tauri dev`), which reported "Path doesn't exist: backend/migrations" and stopped. The path is now anchored to the repo, wherever you start it. (#1420)
- The first generation after startup no longer stalls or 500s while the model is still loading. A cold load reached from a worker thread waited on a lock owned by a different event loop, which either errored outright or deadlocked until the job was abandoned. (#1417)
- The voice-design model on Apple Silicon works again. Its description was being dropped before it reached the engine, so every generation failed with a raw 400 no matter what you typed. (#1405)
- The first-run setup screen no longer times out while it waits for you. Taking more than two minutes to choose an install location, region or mirror made the app declare "Setup failed", and Retry landed back on the same screen with the same clock — so a first install could never be completed. (#1376)
- Transcription on an NVIDIA machine whose cuDNN 8 libraries are missing no longer kills the backend outright. The app checks the library before picking a transcription engine and falls back to PyTorch Whisper, instead of handing off to a component that aborts the process with no error and restarts into the same crash. (#1371)
- The dictation model picker now tells the truth about download size. Every one of the seven models was wrong: Parakeet TDT v3, the recommended default, said 180 MB and actually downloads 670 MB, while the small low-RAM fallbacks were advertised as three times bigger than they are. (#1398)
- Dictation with the 0.6B Parakeet models is steadier under load — they now decode on more threads (still capped by your CPU, still overridable with `OMNIVOICE_SHERPA_ASR_THREADS`). The small models are unchanged. (#1398)
- The dictation hotkey no longer leaves a blank dark square stuck on your desktop. A press that arrived while the pill was re-arming was dropped, and the window it had already opened had nothing in it and no way to close it. (#1398)
- The blank dark square is gone for good: the dictation window could mistake itself for the main window when its shell wasn't ready yet, and once it did, nothing in the app could close it again. It now learns which window it is before any of its code runs. (#1398)
- Dictation is more reliable to trigger: the hotkey listener no longer briefly detaches every time the pill changes state, so a press is never silently lost. (#1398)
- An auto-captured crash report now keeps the error that actually caused the crash. Python prints a chained traceback oldest-first, so trimming the log to its newest end kept the generic wrapper and cut the real cause — the reports that needed the detail most were the ones that arrived without it. (#1376)
- Text ending in punctuation no longer wastes a whole synthesis pass on it. A chunk boundary could leave a trailing fragment with nothing speakable in it, which the engine renders as nothing at all. (#1330)
- A take that is missing part of your text now says so instead of coming back quietly short. When the engine renders a sentence to nothing, the app names the missing text and suggests re-generating — until now the only way to notice was to read along. (#1330)
- A long render on modest hardware is no longer abandoned as "too heavy for the available compute" while it is visibly working. A generate that keeps finishing chunks now extends its own deadline (bounded), the way a model download already could; one that stops producing anything still fails on time. (#1338, #1348, #1391)
- A backend that dies while loading its own Python dependencies is no longer reported as a memory problem. The crash notice now says the environment is incomplete and points at "Clean & Retry", instead of sending users to flush a model that had nothing to do with it. (#1282, #1376)
- A UI whose API requests land on the wrong host — a rehosted frontend, or a reverse proxy with no API route — no longer echoes that host's raw 404 page as the error. It now says the responding server is not a VoiceStudio backend and points at the Backend URL setting and the proxy route. (#1385)
- Building the GGUF engine from source produced a binary that died on its very first spawn ("libggml.so.0: cannot open shared object file") — the build script deleted the shared libraries it had just linked against. It now ships them next to the binary on every platform, and the backend puts that folder on the loader path — thanks @vanderlpp! (#1348)
- The GGUF engine's hard 120-second per-render kill switch — which was reaping legitimate CPU-only renders mid-synthesis — is now 600s, tunable via `OMNIVOICE_GGUF_GENERATE_TIMEOUT_S`, and the timeout error names that setting — thanks @vanderlpp! (#1348)
- Every subprocess TTS engine would have turned a stereo render into noise: the mono downmix always averaged axis 0, which is time rather than channels for channels-last audio. Unreachable today since every engine returns mono, fixed in all five before it isn't. (#1328)
- First-run wizard: the Continue button and the Hugging Face token box were pushed below the window with no way to scroll to them — a layout container grew to the full model list's height, defeating every scroll clamp inside it. The pinned row now stays on screen at every UI scale, with the model list scrolling under it. (#1382, #1383)
- Dubbing the same video twice no longer ties the second job's cloned voices to the first job's files — deleting the older dub from history was silently turning the newer one's single-segment regens into a default voice. (#1331)
- ...and deleting a dub whose files an existing saved dub still renders from now keeps those files on disk (the history entry still disappears) — protecting dubs created before this fix, whose references already cross directories. (#1331)
- An unclean previous shutdown is no longer announced as a crash: the notice says what it actually knows, names the benign causes (sleep, force-quit, a stopped VM), and the one-click bug report is only offered when there is evidence to put in it — an empty report helps nobody. (#1375)
- A first-use generate no longer fails at 300s while its model is still downloading: the download's own progress heartbeats now extend the generation budget (bounded), so a slow connection isn't reported as too-slow hardware. A job that goes silent still dies at the original deadline. (#1367)
- A generation that hits its time limit now says so, instead of "an error VoiceStudio doesn't recognize" followed by an empty `TimeoutError:`. It names the likely causes and the setting that raises the limit. (#1368)
- The "transformers install is incomplete" advice now names torchvision — the package whose version mismatch actually produces that error — and points at the pinned reinstall that repairs it, instead of a reinstall that left the broken package untouched. (#1376, #1357)
- A model download cut off mid-request is no longer reported as a broken transformers install — reinstalling could never have fixed a dropped connection. (#1347)
- A TLS connection cut during generation is explained as the dropped download it is, instead of falling through as an unrecognized error carrying `_ssl.c:1016`. (#1335)
- Windows "paging file is too small" no longer suggests the Flush button, which cannot help. It now names the virtual-memory setting to change, and says plainly that it is not a network problem. (#1334)
- A port conflict that resolves itself while the backend is dying no longer reports a bare "Backend died (exit code 1)" — the conflict is named even when the other process has already let the port go. (#1364, #1223)
- Fresh installs failing to import `transformers.HiggsAudioV2TokenizerModel` with "RuntimeError: operator torchvision::nms does not exist" are fixed by pinning `torchvision==0.23.0` to match `torch 2.8.0` — thanks @HanzlahCh! (#1358, #1357)
- ...and that pin now actually reaches Colab and Docker: both install with `uv pip install`, which ignores the pyproject setting the pin lived in, so the torch trio could still drift apart. It is passed explicitly now. (#1357)
- Every RTX 40-series card (4060–4090) was declared unsupported and silently run on the CPU. The compatibility gate demanded an exact `sm_89` match, but PyTorch ships `sm_86` kernels that already cover Ada. (#1285)
- Under-provisioned hardware is now flagged **before** a synthesis starts instead of after the full compute budget expires. (#1240, #1246, #1248, #1277, #1283, #1284)
- Long text on a CPU-only machine gets the same warning up front. (#1260, #1299)
- A crash inside the compute stack no longer blames VRAM: a segfault or Windows access violation now points at the GPU driver or an incomplete model download. (#1275, #1293)
- ffmpeg failures report the failure instead of ffmpeg's build configuration. (#1309)
- A cut TLS connection is explained in words rather than as `_ssl.c:1016`. (#1301)
- `torch.compile` is skipped when the torch library path contains a space, instead of failing in the linker on every load. (#1266)
- macOS Preview updates work again — the updater bundle had been colliding with itself since early July. (#1281)
- macOS Preview updates no longer fail signature verification — the preview manifest is rebuilt from the published assets and every signature in it is verified against the file it points at — thanks @Pinkers01! (#1327)
- A dub whose transcription stream is cut by a reverse proxy now says so, instead of blaming the ASR model. (#1317)
- The dev backend going quiet under `--reload` is named as auto-reload rather than reported as a crash. (#1261)
- Building from source: `bun run desktop-prod:run`, documented as a re-launch, wiped the app's data every time — voice profiles, projects and outputs included. It now keeps them — thanks @Kakuzen93! (#1333)
- Audiobook: a chapter that fails to render now shows the reason in the chapter list and in the final error, instead of a red row whose cause existed only in the backend log — thanks @Reaksa-Cambodia! (#1321)
- Audiobook: an engine that stops without producing audio no longer stalls the render forever with no error and no timeout. (#1321)
- Linux AppImage: a permanently blank window on Mesa 26.1+ hosts (Arch/CachyOS and other rolling distros) — the bundled WebKit ran against a newer system Mesa than it was built for, and no environment variable could help because the failure precedes every rendering flag; the launcher now lets a newer system WebKitGTK take precedence — thanks @rvasilev and @HannaLovvold! (#1258, #1244)
- Linux AppImage: `OMNIVOICE_PREFER_SYSTEM_WEBKIT=1` forces your own WebKitGTK for hosts where its version can't be read automatically (no `pkg-config`), and `=0` forces the bundled one (#1258)
- Dubbing: the transcription overlay said "Transcribing with Whisper…" whatever ASR engine was actually running — it now names the stage, in all 21 languages — thanks @paoloantinori! (#1352)
- Error messages no longer arrive with terminal colour codes spliced into the sentence (`download: ^[[0;31mERROR:^[[0m …`) — every surfaced failure is cleaned now, whichever tool produced it. (#1344)
- Linux AppImage: recording failed with "No microphone found" on hosts whose GStreamer is newer than the build runner's, even with a verified-healthy audio stack — your own GStreamer now takes precedence, and the plugin cache is app-private so it can neither be confused by nor corrupt the one other apps use — thanks @Kakuzen93! (#1333)
- Linux AppImage: that GStreamer preference actually takes effect — the check guarding it could never pass, so it had been silently doing nothing. (#1333)
- A TTS job abandoned for exceeding its compute budget now records where it was actually stuck, so a hang stops being reported as a machine that is merely too slow. (#1338, #1329, #1348)
- Translation through LM Studio works. The built-in model name was the placeholder `local-model`, which LM Studio rejects because it serves whatever you have loaded — VoiceStudio now asks it, and a 404 from a local server names the models that ARE loaded instead of telling you to check a URL that was fine — thanks @biga73! (#1332)
- Generation that silently dropped the end of the input now says so. When an engine returns no audio for part of the text the result sounds clean and is simply short, so the only way to notice was to read along; the backend log now names the sentences that produced nothing. (#1330)
- Dubbing: a re-rendered line that quietly came back in a default voice instead of the cloned one now says why in the backend log — the clone clips are extracted per job and a saved dub outlives them, so regenerating after cleanup loses the reference with no error. (#1331)
- RTX 40-series GPUs are used again instead of being sent to the CPU. (#1289)
- Apple Silicon: transcription no longer needs a system ffmpeg, as the docs always said — thanks @gambletan! (#1436)
- A failed audiobook chapter says why, instead of turning red and saying nothing. (#1325)

### CI

- Windows CI falls back to a static ffmpeg build when the Chocolatey feed is down, instead of failing the run. (#1542)
- The stdio wire protocol every engine sidecar speaks is now tested once across all nine of them, instead of against a single engine — a bug in any one sidecar's copy gets caught — thanks @paoloantinori! (#1408)
- Windows smoke tests stopped silently passing a broken ffmpeg install, and every smoke leg is now budgeted for a cold dependency install. (#1290)
- Test suites no longer leak config paths or model-manager shutdown state into one another, which had been failing unrelated pull requests. (#1269)
- The nightly preview build stopped refusing to publish its own healthy updater manifest when the macOS legs finished a few minutes ahead of the slowest one — Preview-channel users were silently left without new builds. 

## [0.4.2] — 2026-07-28

**Highlights**

- The update prompt is a small toast with buttons, not a screenful of release notes
- Installing an update no longer throws away work that is still running
- Quitting the app mid-generate stops reporting itself as a crash
- A half-downloaded model repairs itself instead of dead-ending
- "Dismiss" no longer reads as "terminate an employee" in five languages

### Changed

- An available update now announces itself as a toast with **Install and restart**, **What's new** and **Later**, instead of only a dot beside the version number. The release notes stay in Settings → Updates, where there is room for them — a version's notes are the whole changelog section, and rendering them inline is what made the old prompt fill the screen (#1272)

### Fixed

- Installing an update no longer relaunches the app while work is running. The check only knew about dub synthesis, so a restart could silently discard an upload, a transcription, a translation, an export or a standalone synth — and two overlapping synths used to cancel each other's protection. Install is now greyed out while anything is in flight (#1272)
- A half-downloaded model now repairs itself instead of failing with a raw 500. The automatic repair recognised only one of the two ways the loader reports missing weights, so an interrupted download whose subfolder failed to load got neither the repair nor a hint about what to do (#1273)
- Quitting the app with a generate queued reported "500 Internal Server Error: model load skipped: backend shutting down" and offered to file a bug for it. A shutdown is not a fault: the backend now answers 503 with what to do, and no bug report is offered for it (#1276)
- Dub history: clearing a large history while a render was running could still resurrect the deleted job — which markers survived depended on the process hash seed, and an oversized purge could discard a live one (#1252)
- German, Japanese, Russian and both Chinese locales rendered "Dismiss" as the employment sense — "terminate an employee" — on close buttons (#1272)
- The "wait for the current job to finish" message named dubbing specifically, though it now covers uploads, transcription, translation, exports and synthesis; reworded across all 21 languages (#1272)

## [0.4.1] — 2026-07-27

**Highlights**

- AMD GPUs are used again — every ROCm host was silently running on the CPU
- Two synth failures that used to say "an error VoiceStudio doesn't recognize" now say what actually went wrong
- A dub URL ingest that fails on a disk problem now says which folder and why
- A broken audio dependency no longer takes the whole backend down at startup
- A GPU too small for the chosen engine now says so up front, not after a five-minute wait
- A port conflict now says so, instead of "Backend died (exit code 1)"
- A model download that dies at 90% now resumes instead of failing the install
- First run: Continue and the Hugging Face token box no longer sit under the status bar
- macOS 12 (Monterey): the app launches again instead of dying on startup
- Exporting a voice or a dub no longer fails when the name isn't spelled in Latin letters
- Two more failures that used to arrive as raw OS text now say what to do about them
- Unload works on every model the panel offers it for, and a language the active engine can't speak says so
- Deleting a dub no longer un-deletes itself when the job it belonged to finishes

### Changed

- First run: the status bar (Logs, version, Sponsors) appears once you reach the studio, instead of overlaying the setup steps (#1241)

### Added

- `OMNIVOICE_MCP_ALLOWED_HOSTS` — comma-separated host patterns (e.g. `host.containers.internal:*,192.168.1.5:*`) that extend the MCP SDK's DNS-rebinding allowlist, so AI agents running in Docker containers or on other machines can reach the `/mcp` endpoint. The SDK default is localhost-only; this env var is opt-in (#1249)

### Docs

- Linux install: a new section for the Mesa 26.1+ blank window, stating plainly that no environment variable works and why (#1258)
- Docker: ROCm section explains that `torch.cuda.is_available() == True` isn't proof the app is on the GPU, and notes the `--group-add` needed for `/dev/kfd` on rootless hosts (#1228)

### Fixed

- Deleting a dub while it was still importing crashed the import with the toast `ingest: 'mgw39lx3'` — a dict key and nothing else — and the delete could then be undone by the job's own pending write, in history or mid-render; both are fixed, and no failure can present itself as a bare value again — thanks @dustmaker124-ui! (#1252, #1253)
- macOS 12 (Monterey): the app threw on startup and never started the backend — it called a Safari 16 method on the WebView that macOS ships. It launches and works now; some styling still needs a newer WebView (tracked in #1268) — thanks @singhrahat! (#1245)
- Settings → Engines: Unload failed with `400 Unknown model id: engine:kittentts` on any in-process engine — the panel offered the button for ids the backend never accepted; the warm dictation model had the same gap — thanks @JavaxmI! (#1247)
- Picking a language the active engine can't speak recited 23 codes without saying which engine refused or that switching engine was the fix — thanks @pulananave! (#1257)
- A YouTube import that failed as "DRM protected" and then worked on a manual retry now escalates the player client automatically, and a genuinely undownloadable video says so — thanks @gysahlgreene! (#1254)
- Exporting a voice profile, persona, dub, subtitle or stem whose name is Chinese, Japanese, Korean, Cyrillic, Greek, Hebrew or emoji failed with a `'latin-1' codec` 500 — every download endpoint now sends the name correctly, and browsers get the real one back — thanks @zvxzdx! (#1262)
- A synth that failed because ffmpeg/ffprobe wasn't on the system path said "an error VoiceStudio doesn't recognize"; it now names the media engine and points at Settings → Audio tools, and the app's own copy is published on PATH so dependencies find it in the first place — thanks @Heuvelsma! (#1256)
- Windows "The paging file is too small" arrived as a bare 500; it now explains that this is a virtual-memory setting, not full RAM, and gives the steps to raise it — thanks @trankeny545-sudo! (#1251)
- AMD/ROCm: every ROCm host was silently force-routed to the CPU — the compatibility gate compared a CUDA `sm_` tag against a ROCm build's `gfx` list, which can never match — thanks @simmessa! (#1228)
- AMD/ROCm: `torch.compile` was disabled on all AMD hosts by the same mismatched comparison (#1228)
- AMD/ROCm: `HSA_OVERRIDE_GFX_VERSION` is auto-set only when your card genuinely needs it and the remap target exists in your build; gfx1150/gfx1151 (Strix Point/Halo) added to the map (#1228)
- Windows blocking an engine file (Smart App Control, WDAC, or AppLocker) is now named, with the fix for personal and managed PCs — thanks @AdityaHemantBhat! (#1227)
- A failed audio write (`LibsndfileError: System error.`) now names the target file, its folder's writability and the drive's free space — thanks @morozov28061995-boop! (#1221)
- Dub URL ingest: a disk error now names the job folder, its writability and the drive's free space, instead of pointing at the system TEMP folder it never used — thanks @dustmaker124-ui! (#1225)
- Dub URL ingest fails immediately when the job folder is missing or unwritable, instead of starting a download that can only fail (#1225)
- The backend no longer dies at startup when transformers can't resolve its audio tokenizer (a missing or mismatched torchaudio, common on Google Colab) — it starts, and the error arrives with a repair hint — thanks @Navdeep-Chauhan-777! (#1229)
- Importing `omnivoice.utils.*` no longer drags in torch, torchaudio, transformers and the full model definition — thanks @Navdeep-Chauhan-777! (#1229)
- Colab notebook: the install cell now catches a broken environment with the real error, instead of a 5-minute health timeout two cells later — thanks @Navdeep-Chauhan-777! (#1229)
- A GPU with less VRAM than the chosen engine needs is flagged in Settings → Engines before you generate, instead of showing a clean green "accelerated" until the job times out — thanks @AdityaHemantBhat and @beingavais! (#1226, #1222)
- A generation timeout now names your actual card and its VRAM and recommends a lighter engine (#1226, #1222)
- First run: Continue and the Hugging Face token box rendered underneath the status bar, off the bottom of the window — the wizard laid itself out against the viewport instead of its own frame (#1241)
- A busy port 3900 now reports a port conflict instead of "Backend died (exit code 1)", in every language — thanks @xipb14! (#1223)
- The app verifies it actually freed the port before starting the backend, rather than assuming the kill worked (#1223)
- A model download truncated near the end is now retried and resumed instead of aborting the whole install — thanks @Reaksa-Cambodia! (#1224)
- Engine first-use downloads (VoxCPM2, MOSS-TTS-Nano) retry transient network failures instead of failing the load outright (#1224)
- A backend killed by the OS mid-stream now leaves a low-memory trail in the crash report (#1224)

### CI

- The AppImage launcher's unit tests now run in CI — they existed but nothing executed them (#1258)

## [0.4.0] — 2026-07-21

**Highlights**

- Audiobooks, end to end — a real **Stop** with live per-chapter progress, a **multi-voice cast**, expressive controls, a markup toolbar, live stats, and a one-click sample
- Pick a designed voice from the **Gallery** anywhere you choose a voice — audiobook, Stories, and Dubbing
- Dub **Paste Translation** — drop in a translation or `.srt` and it maps straight onto your segments, timings intact
- Downloading a finished audiobook no longer hijacks the app — it just saves
- First run is ~2.4 GB, not ~5 GB — only the TTS model is required; ASR picks are curated per platform
- Guided mic + Accessibility permissions with Open Settings deep-links; **Parakeet TDT v3** on Apple Silicon
- Opens in your system language, with a one-tap switch back to English
- Security: server-mode admin routes can't be reached by a trusted-network client without the API key
- A render error shows a recoverable card instead of a blank window; queued and long generations stop failing with a bogus "too heavy for your hardware"

### Changed

- Settings → Models: grouped catalog (TTS / ASR / Dictation / Diarisation), "recommended for this machine" chips, incompatible models collapsed behind a toggle
- Only the TTS model (~2.4 GB) is required on first run; ASR picks are curated per platform via `curated_on` in `models.yaml` (MLX on Apple Silicon, CT2+Turbo on CUDA, PyTorch on ROCm, int8 on CPU)
- Audiobook tab tidied up: the settings column is now grouped into compact collapsible sections (Output / Book details / Pronunciation / Markup), so script + voice + Create sit up top instead of a long scroll — same controls, denser layout (#1214)

### Removed

- The Dubbing per-segment picker's hardcoded design-presets group — superseded by the richer designed-voice Gallery; already-saved `preset:` picks still generate identically (#1220)

### Added

- Voice picker: the designed-voice **Gallery** is now selectable anywhere a voice is chosen — the audiobook default voice and each Cast row can pick a gallery archetype (searchable, favourites first), and it's materialised into a real profile on pick so it just works everywhere (#1219)
- The Stories editor and the Dubbing per-segment voice pickers now use the same gallery-enabled picker, so designed-voice archetypes are selectable there too; the dub picker drops its redundant hardcoded presets group in favour of the richer Gallery (existing picks unchanged) (#1220)
- Audiobook tab: a Cast panel maps each `[voice:NAME]` in the script to a profile so multi-voice renders correctly (it previously fell back to a single voice), plus a markup insert toolbar, live stats (chapters · words · est. runtime), and pre-flight validation for unknown voices and empty chapters (#1217)
- Audiobook tab: a **Stop** button that truly cancels a running generation (not just the UI) and live per-chapter progress — a bar, elapsed + ETA, and each chapter's status (rendering / done / cached / failed); finished chapters stay cached so Create again resumes. `Cmd/Ctrl+Enter` starts a render (#1216)
- Settings → Permissions + wizard System Check: live mic/Accessibility grant state, per-OS guidance, Open Settings deep-links; dictation pre-flights the mic grant (#1175)
- `parakeet-mlx` engine: Parakeet TDT v3 on Apple Silicon — 25 EU languages, word timestamps, ~2 GB, opt-in from Settings → Models, never auto-downloads (#1175)
- First-run downloads race the direct GitHub path against the mirror and use whichever answers fastest (#1179)
- First-run consent question for the existing opt-in analytics (two equal buttons, skip = no)
- First run: when the app auto-opens in a non-English system language, a one-time, dismissible banner offers to switch the UI to English — shown only until you pick a language, never for English systems (#1215)
- Source builds carry the publishable analytics token and get the same first-run consent ask as installers; opt-in events now note the install channel (installer / docker / source) — thanks @agudmund! (#1193)
- Official Google Colab notebook (`notebooks/VoiceStudio_Studio_Colab.ipynb`) — full app + API feature tour on a free T4
- ROCm Docker image `ghcr.io/debpalash/omnivoice-studio:rocm` (+ `:stable-rocm`, `:X.Y.Z-rocm`) (#1165)
- `OMNIVOICE_TRUSTED_NETWORKS` — comma-separated CIDRs exempted from the consumption auth gates (share PIN / API key / dictation WS); admin routes stay loopback-only (#1170)
- Info/warn system notifications are dismissible and stay dismissed across restarts; error-level notices can't be dismissed, and the unclean-shutdown notice is now acknowledged server-side — thanks @agudmund! (#1192)
- `clone_voice` MCP tool — AI agents can clone a new voice from a base64 reference audio sample; returns a `profile_id` immediately usable with `generate_speech` — thanks @paoloantinori! (#1194)
- Dub tab: **Paste Translation** — paste a translation made elsewhere (ChatGPT, DeepL, a human) as subtitles, numbered lines, or plain lines; it maps onto the existing segments with a before→after preview, keeping timings and the source transcript intact (#1203)
- Audiobook tab: **Production Overrides** (position/class temperature, steps, guidance, postprocess, seed) for expressive narration, plus IndexTTS2 emotion controls and a "vary repeated lines" toggle — defaults reproduce today's renders exactly (#1208)
- Audiobook tab: a **Load sample** button that fills the editor with a demo story — chapters, per-character `[voice:]`, `[pause]`, `[slow]`/`[fast]`/`[emphasis]`/`[spell]`, and reaction tags — so first-timers can hit Create and hear every capability before their real work (#1214)

### CI

- The quiet changelog style and 21-locale key/placeholder parity are now enforced by plain pytest checks; CodeRabbit/Greptile carry the house rules via `.coderabbit.yaml`/`greptile.json` (#1198)

### Docs

- `docs/expressive-speech.md`: per-engine breaths/laughter/emotion control, incl. the default engine's 13 native reaction tags
- Flush caches / Unload documented in the performance guide, incl. `POST /system/flush-memory` for scripts
- README FAQ: why a longer reference clip doesn't clone better (zero-shot 15 s cap; fine-tuning is the audiobook-grade path)
- `docs/expressive-speech.md` corrected so every recipe it names (breaths, temperature) is reachable in the surface it points to, including the Audiobook tab (#1208)
- New `docs/api-auth.md` — one place for authenticating the local API: share PIN, API key, dictation WebSocket, and trusted networks, with curl/SDK examples and what `401`/`403`/`429` mean (#1212)

### Fixed

- Downloading a finished audiobook (or story mix) no longer hijacks the app: in the desktop WebView a plain download link to the media file made WebKit navigate the whole window to it and play it fullscreen (then the blank-window guard misfired) — downloads now go through the native Save dialog + a server-side copy instead (#1218)
- The blank-window guard's fallback page is shown by injection rather than a `data:` URL the desktop WebViews refuse to navigate to, and its Reload button now returns to the app even if the window had navigated away (#1218)
- Security (server mode): the admin routes (`/system/*`, `/api/settings/*` — RCE-class) now require the API key or genuine loopback — with an API key set and `OMNIVOICE_TRUSTED_NETWORKS` configured, a trusted-network client could previously reach them with no credential; the short share PIN no longer gates admin either (#1213)
- A render error no longer blanks the whole window — a recoverable error card (Reload / Report) appears instead, and CI now builds the real production bundle so a pre-mount crash can't ship (#1209)
- Voice-clone trimmer: the preview now plays exactly the selected region on variable-bitrate clips (it had drifted off on VBR/mis-reported-duration files by playing the original file on a different timeline) (#1210)
- Screen readers now announce the hidden file-picker buttons (batch add, gallery import, stories import) (#1211)
- Audiobook language selection now reaches the backend — the client had dropped the `language` field, and the tab's Markup reference now lists the reaction tags (`[laughter]`, `[sigh]`, …) that already work there (#1208)
- A backend that fails to start now says why — exit code and error output, with actionable hints and a one-click report — instead of the evidence-free "Can't reach the local VoiceStudio backend" (#1177)
- Generation no longer crawls on CPU after a cancelled or failed dub: the TTS model is moved back to the GPU on every exit path, and each generation now verifies its own placement (#1191)
- A generation queued behind a busy one no longer spends its timeout waiting: the budget starts when a GPU worker picks the job up, so a queued request can't be failed as "too heavy for the available compute" without having run (#1190)
- One request's timeout no longer cancels unrelated jobs already waiting in the GPU queue (#1190)
- Timeout messages stopped claiming capacity was restored automatically — the abandoned job keeps the device until it finishes, and the guidance now says to let it drain (#1190)
- The length-scaled generate budget now covers every path — streaming previews, batch dubbing, `/v1/audio/speech`, dub and archetype previews — instead of only the two classic call sites, so long inputs stop failing at a flat 300s (#1190)
- Provenance watermarking moved off the GPU worker pool: on 1-worker machines each embed was serializing ahead of the next generation (#1190)
- A batch segment that times out fails the job with a reason instead of shipping a finished-looking dub with silent gaps (#1190)
- `/v1/audio/speech` refuses work up front with 429 + `Retry-After` when the pool is saturated, and returns a retryable 503 rather than a 500 on timeout (#1190)
- Subtitle parsing no longer stalls on a blank-line-heavy `.srt`: the timing-line regex backtracked across newlines, so a mis-saved export could pin an import for hours (#1203)
- A broken ASR engine's fallback could silently auto-download multi-GB weights — every fallback now passes the same no-download preflight and shows the download CTA instead (#1189)
- Dub transcription releases the ASR model from VRAM on every exit — crashes, early errors, and client disconnects included (#1175)
- An invalid dictation model override could bypass the missing-model check for the Whisper fallback (#1175)
- The OpenAI-compatible transcription route's 409 now carries the same typed download-CTA payload as every other route (#1175)
- Cross-drive installs with a user-pinned `UV_CACHE_DIR` still keep uv's managed Python off the system drive (#1189)
- MCP `clone_voice` accepts data-URI base64, stores the clip under its real container extension, and returns backend validation errors as structured JSON (#1195)
- Sidecar launch errors no longer embed the user's home directory in logs or error messages (#1189)
- KittenTTS degrades gracefully if a future upstream update moves its text chunker (#1189)
- Restored 151 broken locale strings: mangled `{{placeholders}}` (vi/ar showed literal `_V_0__`) and gallery errors that dropped their detail in all 20 translations (#1198)
- Voice cloning no longer fails on quiet recordings — silence removal retries with gentler thresholds (then skips), and a truly silent clip gets a localized, actionable message instead of a dead-end 400 (#1188)
- Custom install folders on another drive are honored end-to-end — uv's wheel cache and Python download now follow the chosen environment folder instead of filling C: (#1186)
- Dubbing no longer fails outright when an ASR engine's dependencies are broken ("No module named 'lightning_fabric'") — the engine is marked unavailable with a repair hint and the next one is used automatically (#1185)
- `/v1/audio/speech` no longer 500s with a raw "Exec format error" when `bin/` holds a zero-byte GGUF placeholder — managed binaries are validated before exec and broken ones return an actionable 400/503 naming the repair (#1172)
- KittenTTS no longer aborts with "invalid expand shape" on digit-heavy input and no longer 500s on empty input — chunks are split to the ONNX 512-token cap and unspeakable text returns a clear 400 (#1173)
- Quitting the app while a model is still loading now shuts down clean — no "cannot schedule new futures" traceback, no crash-shaped exit or phantom crash record, and first-run/upgrade boots keep their backend log (#1174)
- Dictation falls back to the main ASR engine when a model transcribes real speech to nothing (sherpa NeMo-TDT decoder defect, upstream k2-fsa/sherpa-onnx#3767), remembers the demotion, and names the model that failed (#1175)
- A failed dictation session with no transcript clears the floating pill instead of parking it on screen forever (#1175)
- A TTS-only first run gets a one-click ASR download prompt instead of a silent 1.6–3 GB Whisper pull (dub, batch, dictation, clone-ref, `/v1` STT, boot warm-up) (#1175)
- App boot makes no Hugging Face calls and never silently downloads the TTS model — warm-up is local-cache-only (#1175)
- Quitting with a batch dub in flight no longer hangs shutdown (#1175)
- Dubbing's vocal-separation step no longer crashes on Windows dev runs (`SelectorEventLoop` sync-pipe fallback) (#1184)
- Closing the app while the model is loading is logged as a shutdown, not a phantom "Model loading failed" crash (#1183)
- The backend-crash banner sits below the navbar and stays clickable (#1182)
- Windows source builds: `bun desktop` no longer dies on a `UnicodeEncodeError` from piped status glyphs (#1181)
- The Windows app no longer boots to a black screen (production-minifier variable reorder in the splash; now gated by a real minified-bundle e2e) (#1178)
- No more storms of black console windows on Windows — every subprocess, incl. third-party spawns, runs windowless (#1178)
- `bun desktop` self-heals a stale terminal `PATH` without `~/.cargo/bin` and hints when Rust is genuinely missing (#1180)
- FunASR/SenseVoice: no more crash or speaker-identity swaps across 30 s chunks with inline diarization (#182)
- Provenance watermark now applied on every synthetic-audio route via one chokepoint (#1169)
- "Can't reach the local backend" reports unclean backend deaths with evidence in dev/Docker/LAN too (#1164)
- "Setup failed" screen renders instead of crashing — thanks @bultodepapas! (#1159)
- Backend error logs keep stack traces (swept across 20 sites) — thanks @bultodepapas! (#1160)
- Reference-clip uploads can't hang on a stuck audio probe (10 s timeout) — thanks @bultodepapas! (#1162)
- Malformed EPUB chapters import partially instead of vanishing — thanks @bultodepapas! (#1161)
- Failed sidebar fetches are logged instead of silently showing stale/empty lists — thanks @bultodepapas! (#1158)
- A missing/broken `mcp` package degrades to "/mcp disabled" instead of killing the backend at startup (#1156)
- "Setup failed" auto-dismisses when the backend recovers; relaunching retries instead of refocusing a dead window (#1156)
- Ended the `forrtl: error (200)` mid-session Windows crashes (math-runtime console handler) (#1153)
- Non-Latin text can't crash synthesis on Windows (backend forced to UTF-8) (#1155)
- Video-export errors diagnose the real cause (Windows 32k command-line limit; big filter graphs go via a script file) with per-mode advice (#1152)
- Remote backends with an API key get an API-key prompt (durable per-browser, one-shot `#api_key=` link) instead of an unpassable PIN form — thanks @paoloantinori! (#1154)

## [0.3.22] — 2026-07-14

The dubbing release. Dubbed videos stop sounding like a compromise: the music keeps its stereo width and full frequency range, short lines no longer leave dead air while the mouth keeps moving, one speaker stays one voice, and the language tabs finally switch the transcript with the audio. Underneath it, the memory fixes that ended the "can't reach the local backend" era on 16 GB machines ship at last — plus a sweep of never-again hardening drawn from an audit of every bug this project has ever closed.

### Added

- **A "Voice match" toggle for dubbing — keep one steady voice per speaker.** Each dubbed line clones from a snippet of its own original audio, which matches the delivery beautifully but can make the *voice itself* drift from line to line — most audibly on videos where speaker detection ran in fallback mode ("still 4 segments different in voice", as one report put it). A new control next to the Timing picker chooses: **Per line** (the default, unchanged) for the best per-line delivery match, or **Consistent** to clone every line of a speaker from one shared reference — the speaker's pooled sample, or the best single clip when none exists — for a steady identity across the whole dub. Flipping it honestly marks segments as needing regeneration, and the shared reference is encoded once and reused, not re-studied per line. (#1147)

- **A performance guide, at last.** [docs/performance.md](docs/performance.md) explains where generation and dubbing time actually goes, the three classic causes of "it got slow" (an empty Transcript field on a voice profile chief among them), every tuning knob the backend reads — none of which were documented anywhere — and which settings to leave alone (raising `OMNIVOICE_GPU_WORKERS` on a small GPU is how you get the crash the default exists to prevent). Includes how to run the built-in profiler so a slowness report can carry numbers instead of vibes.

- **In-app analytics is now wired end to end — and still off until you say yes.** The frontend analytics SDK is only ever started *after* you opt in (Settings → Privacy), never at app launch, so a default install still transmits nothing. Two of the SDK's defaults are explicitly disabled because they would be actively harmful here: **autocapture**, which sends the text content of whatever you click — in this app, the script you are about to synthesise, your voice names, your file names — and **session recording**, which records the screen. Events carry metadata only, filtered through the same allowlist as the backend, so no future change can leak your content by adding a field.

- **Opt-in analytics — off by default, and it can't lie to you.** VoiceStudio still sends **nothing** out of the box: no accounts, no telemetry, no phone-home, and your text, audio, voices, and projects never leave your machine regardless of what you choose. There is now one toggle in **Settings → Privacy → "Help improve VoiceStudio"**, **off unless you turn it on**. If you do, it sends anonymous usage stats — which engine and language you used, how long a generation took, how many *characters* the text had (a number, not the text), and the *type* of any error. It never sends the text you type, your audio, your file names, your voice names, or anything identifying you. That isn't a promise in a policy: an **allowlist in the code** drops any property that isn't on it, so a future change can't leak content by accident, and crash tracebacks are deliberately **not** auto-captured (they can carry file paths and tokens). Turning it off stops everything immediately. Builds from source have no analytics destination at all and don't even show the toggle.

- **Settings → Usage: see what you've made, counted entirely on your own machine.** Takes generated, audio produced, voices, days used, and a breakdown by mode and language — all computed from the history already in your own database. It collects nothing new, stores nothing new, and transmits nothing anywhere, no matter what you've chosen under Settings → Privacy: this panel is *yours*, it works with analytics switched off, and it never phones home. If you want to know what you've been making, the answer shouldn't require sending it to anyone.

- **The memory panel now tells the whole truth.** `Settings → Models` (and `GET /model/loaded`) used to report only the VoiceStudio core model — a resident second engine like MLX-Audio, or the warm dictation model, was invisible, so the memory picture looked ~2 GB lighter than reality. It now lists every resident model (in-process engines and the dictation ASR included) and adds a system block with free/total RAM (and free VRAM on a dedicated GPU) plus a low-memory warning. On top of that, a load that starts while memory is already low leaves a breadcrumb in the backend log, so a subsequent out-of-memory kill points at the load that tipped it instead of dying silently. Advisory only — nothing is blocked (the OS can reclaim memory, and refusing a load on an estimate would brick machines that would actually cope). Tune the threshold with `OMNIVOICE_LOW_MEMORY_HEADROOM_GB` (default 2).

### Fixed

- **Switching preview languages can't leave a mixed-language transcript.** Follow-up to the tab/transcript sync: if a track's translations were only partially stored in the browser (older projects, partial regenerations), switching tabs could show German audio with a few rows still in the previous language. Missing rows now hydrate from the app's own per-language store on the backend — and a picked regional dialect is automatically cleared when you switch to a language it doesn't belong to, wherever the switch comes from. (#1149)

- **The Export step's language tabs now switch the transcript too.** Clicking Bengali/German/Hindi… above the finished dub swapped the *video* but left the segment list showing whichever language you generated last — German audio over Bengali text. The tabs now also swap every segment's text to that language (through the same per-language store the language picker uses, so nothing is lost when you switch back); the Original tab keeps your editing language as-is, since each row already shows the original line beneath its translation. (#1148)

- **A "backend crashed" notice can no longer outlive the update that fixed the crash — and the desktop shell's self-repair paths are now pinned by tests that CI actually runs.** Crash notices now record which app version wrote them, and a notice left behind by an older version is ignored and cleaned up after you upgrade instead of resurfacing as if the new build had crashed. The Windows blank-window repair (the one-click WebView cache fix after a BSOD) also gets regression tests pinning its safety contract — one attempt per request, never touches anything unasked, never blocks startup on a locked cache — and CI now runs the desktop shell's entire Rust unit-test suite on macOS, Windows, and Linux, which it previously never executed at all. (#1145)

- **The MLX-Audio phonemizer's language model now ships with the app environment instead of being fetched mid-generation.** Follow-up to the pip fix: with the installer present, the first English MLX-Audio generation would auto-download a small model straight from GitHub — an outbound request that bypasses the app's mirror system (a problem on restricted networks) and fails offline. The model is now a pinned dependency of the managed environment: it arrives at install/update time through the normal dependency flow, and first generation works fully offline. (#1146)

- **The MLX-Audio engine's first English generation no longer trips over a missing installer.** Its phonemizer auto-downloads a small language model on first use by shelling out to `pip` — which the app's managed Python environment didn't include, so the download always failed (and before the recent containment fix, took the whole backend down with it, #1133). `pip` now ships as a real dependency of the managed environment, so it survives app updates too — anything installed ad-hoc would have been stripped by the updater's environment sync, quietly re-breaking this after every release. (#1144)

- **A voice engine's helper library can no longer shut down the whole backend.** One user's backend died 21 seconds after starting (#1133): the MLX-Audio engine's phonemizer tries to auto-download a language model on first use, the downloader is written as a command-line tool, and on failure it calls "exit the program" — which, running inside the backend, exited *the backend*. Any engine dependency written that way could do this. Exits are now contained at the engine-dispatch boundary and turned into a normal, explained error ("an engine dependency failed to auto-install something — see the log"), for TTS and transcription alike. The app keeps running; the failed request tells you what actually happened. (#1143)

- **Vietnamese years read like Vietnamese again.** A recent release started spelling out numbers before synthesis, and its Vietnamese number library turns out to be wrong for exactly the numbers people say most — years ("2024" became *"hai nghìn lẻ hai mươi bốn"*, which no Vietnamese speaker says). The voice model has always pronounced Vietnamese digits correctly on its own, so Vietnamese text now keeps its digits — the same conservative rule that already protected Vietnamese decimals. Also closes the loophole that made this depend on spelling: picking "Vietnamese" from the language list behaved differently from the code "vi". (#1139)

- **A voice profile's pinned seed now pins Audiobook renders too.** Locking a take (or a designed voice) stores a seed so the voice performs reproducibly — and the Voice page honors it, but Audiobook/Stories renders quietly ignored it and rolled fresh randomness for every segment. Book renders with a pinned-seed profile are now deterministic end to end, matching the Voice page. And the audiobook renderer's higher generation quality (32 decoding steps — the model's own quality preset, vs. the Voice page's fast default of 16) is now pinned explicitly in code rather than inherited by accident, so it can't silently change; that steps gap is also *why* Audiobook sounds steadier than Voice at default settings — move the Voice page's Steps slider to 32 for the same quality. (#1139)

- **A finished audiobook's Download button stops vanishing.** The player and Download link for a completed book lived only in the page's temporary state — switch tabs once and they were gone, which read as "no way to export at all" (the file was still on disk, and in Projects → Audiobooks). The last finished render now survives tab switches and reloads, right where the book was made. (#1139)

- **Six recurrence guards from a full audit of the project's issue history — aimed at "this bug can never come back, even after an update or reinstall."** (1) Before loading the voice model on a memory-tight machine, the app now *first releases* things it already reclaims on idle (the warm dictation model, allocator caches) — the missing half of the 16 GB OOM-kill fix; roomy machines pay nothing. (2) When the operating system force-kills the backend for running out of RAM, the crash notice now says exactly that instead of blaming "VRAM" on machines that have none. (3) Saving a *cloned* voice with free-form text in its delivery field can no longer persist a profile that errors on every future generation — the server now sanitizes all profile kinds, closing a hole that had been re-exploited three times through different clients. (4) A reinstall that inherits an old settings file pointing at an unplugged drive or deleted folder no longer sends downloads into the void — dead paths are ignored for the run with a clear log line. (5) Locally-saved UI state is now schema-checked as a whole on restore, so one corrupted field can't silently discard everything after it (the general form of the "app got empty" fix). (6) File moves across drives (Windows D:-drive installs) get a dedicated safe-move helper, so the next code path that renames across devices degrades gracefully instead of failing with `[Errno 18]`. Long texts also get a generation time budget that scales with their length instead of a fixed five minutes. (#1141)

- **Dubbed videos get their stereo back — and the music's full frequency range.** A/B-measuring a dub against its original showed the dubbed audio was **mono in a stereo container** (channel correlation 1.000 vs the original's 0.754) — the entire stereo image of the music, gone. Two causes, both fixed: the separation step was being fed the **16 kHz mono** file extracted for transcription — so the music bed inherited mono *and* an 8 kHz ceiling at the source — and the mixer then let the mono voice drag the whole mix down to mono. Ingest now makes a second, full-quality stereo extraction (44.1 kHz) just for separation, transcription keeps its mono file, and the mixer pins both sides to stereo with the voice dead-center where dubbed dialogue belongs. Loudness already matched the original (−17.2 vs −17.8 LUFS, measured); now the width and brightness do too. (#1138)

- **Dubbed lines that finish early no longer leave dead air — they now speak at the pace of the scene.** Translations routinely come out shorter than the original delivery, and the dub used to just stop early: measured on a real dub, **8.8 of 18.7 seconds of speech time had no voice at all** — the mouth kept moving on screen over the thin residue the vocal separation leaves behind, which reads as silence and as "the music got quiet". Short lines are now gently slowed toward their time slot (pitch preserved, never below 0.85× — comfortably natural), so speech covers the speaking time the way the original did. This also does most of the work people expect from "lip sync": the voice now starts *and ends* with the mouth. Near-full lines are left untouched, the per-segment badge shows the applied rate, and `OMNIVOICE_UNDERRUN_MIN_RATE=1.0` turns the fill off. (#1137)

- **The dub's background music no longer comes out quiet and muffled.** Every dub export mixes your synthesized voice over the video's separated music/ambience bed — and that mix had two fidelity bugs stacked on top of each other. The mixer *normalizes* its inputs, so the weights meant to gently favor dialogue actually played the music at **~57% of its original level** (measured); and because the voice track is synthesized at 24 kHz, the mixer silently pulled the 44.1 kHz music down to 24 kHz — deleting everything above 12 kHz: cymbals, brightness, air. The batch pipeline was harsher still, pinning the bed near 8%. All six mix sites now share one filter that resamples both sides up to 48 kHz, cancels the normalization so the music plays at **90% of its true level** (a hair of headroom keeps dialogue legible), and adds a transparent peak limiter. Measured on a real dub: bed level 57% → 90%, bandwidth 12 kHz → 24 kHz. (#1136)

- **A rate-limited translation polish pass no longer sabotages the dub — or lies about it.** The Cinematic quality mode runs an optional critique-and-rewrite pass after translating. When that pass hit a rate limit (free-tier LLM endpoints throttle hard), three bad things happened at once: the app reported **"N/N segment(s) failed"** in red over a translate that had actually succeeded; the affected segments were **silently skipped by the speech-rate fit pass and duration planner** — so overlong lines went to synthesis unfitted and came out audibly time-compressed; and the two-second "retry shortly" hint the provider sent was ignored. All three are fixed: a rate-limited call now waits out the provider's own `Retry-After` (bounded, once) and usually just succeeds; a segment that still misses the polish keeps its plain translation, **stays in every downstream fitting pass**, and is reported honestly — "translated, polish skipped" as a warning with the reason, not a failure. Rows that really failed still say so. (#1135)

- **Dubbing kept re-studying the same speaker's voice, hundreds of times per video.** Each dubbed line clones from a clip of its own source audio (that's what makes deliveries match), and lines too short to clone from fall back to a per-speaker sample. But the app's memory for already-studied voices only holds 8 — and a long dub streams *hundreds* of one-shot per-line clips through it, each pushing out the per-speaker samples that every other line needs. Result: the speaker sample was re-studied (~0.4 s, measured) over and over. One-shot clips are now studied without displacing anything, so the per-speaker samples stay warm for the whole dub. Nothing about the audio changes — same clips, same voices, less repeated work. (#1132)

- **Clicking "Install" on an engine right after opening Settings could silently do nothing.** When the Engines page opens, it quietly checks each installable engine for an in-flight install to re-attach to. If you clicked Install while that check was still running, your click's status update was thrown away to keep requests orderly — so no progress panel, no error, no retry, just nothing (the install itself *did* start in the background; the UI simply never showed it). Fast machines usually won the race, which is why this mostly showed up as a once-in-a-while CI test failure. The Install click's update can no longer be dropped — it politely waits out the startup check instead. (#1131)

- **Cloning re-listened to your reference clip for every chunk of text — now it listens once.** Before VoiceStudio can speak in a cloned voice it has to *encode* the reference clip you gave it. That encode was being redone on **every single piece of the job**: long text is split into chunks, and each chunk re-encoded the same reference from scratch; so did each `[pause]` span, and each chapter segment of an audiobook. A cache to prevent exactly this was written a while back — and then quietly bypassed on the path the Generate button actually takes, so for several releases it only ever helped the API. It's now wired into every path. Measured on an M2, one encode costs **0.4 seconds**, so this gives back roughly **3–4 seconds on a long paragraph** and **about a minute on a 166-segment audiobook** — the same voice, the same audio out, just without listening to your reference clip 166 times. As a bonus, `preprocess_prompt` on the OpenAI-compatible endpoint now actually does something; it was being accepted and silently discarded. (#1130)

- **Dubbing loaded the 3 GB voice model, threw it away, and loaded it again.** Before transcribing, a dub pulled the entire voice model into memory to read a single setting off it — one that is empty unless you've turned on an off-by-default flag. So it loaded ~3 GB, found nothing, released it a moment later (on Apple Silicon that's a *full* unload), and then had to load the very same model again from cold when it was time to actually speak. Every dub paid for that round trip — roughly **8 seconds**, plus the memory churn on exactly the 16 GB machines where memory pressure is the problem. It now only loads the model when there's genuinely something to read. (#1130)

- **The backend stopped holding the voice model hostage while it loads the transcription model — the 16 GB dub crash.** Before transcribing a dub, VoiceStudio makes room by setting the TTS model aside. On an NVIDIA GPU it did. On **Apple Silicon it did nothing at all** — the code bailed out with "unified memory doesn't benefit from offloading". That was half right and wholly wrong: on unified memory, *moving* a model to "CPU" frees nothing (it's the same RAM), but the answer is to **release** it, not to skip the step. So a 16 GB Mac went into a dub holding the ~3 GB voice model, then loaded a ~3 GB transcription model on top of it — measured here: 4.1 GB free before, and large-v3 needs 3 — and the operating system killed the backend mid-transcription. That's the dub that "dropped before emitting any segments". The voice model is now genuinely released when memory is tight (and left alone when it isn't, so a roomy machine pays nothing); it reloads by itself on your next generation. (#1119)

- **Dubbing on a Mac was transcribing on the CPU — with the GPU sitting idle.** VoiceStudio picked its transcription engine without ever looking at your hardware: WhisperX won every time, and WhisperX (like faster-whisper) is built on CTranslate2, which **has no Metal backend at all**. So on Apple Silicon it ran whisper-large-v3 on the *processor*. Measured on an M2, one 30-second chunk: **90 seconds on the CPU versus 20 on the GPU** — slower than realtime, which turned a 16-minute video into a ~48-minute transcribe that looked exactly like a hang. Worse, the slowest chunks blew past the 2-minute per-chunk timeout and were **abandoned entirely**, so the transcript came back with pieces missing and the app blamed a "VRAM-starved GPU" — on a machine that has no VRAM. Apple Silicon now uses MLX, which runs the **same** whisper-large-v3 on the GPU, roughly **4x faster**. Word timing is unchanged: the wav2vec2 forced alignment that lip-sync depends on (±10-30 ms, versus Whisper's own ±100-300 ms) is layered on top exactly as before. Same model, same alignment, four times the speed. Nothing changes on NVIDIA or Linux, where WhisperX already used the GPU. (#1127)

- **The transcribe screen invented its ETA, and the number was a fiction.** It assumed transcription runs at ~20x realtime — true on a fast GPU — and predicted from the video's length alone. For a 16-minute video it promised **56 seconds**. Once reality overran the guess it pinned itself at "~0s remaining" with the bar frozen at 95%, and sat there for the next three quarters of an hour. It now reports the *real* fraction of the audio transcribed and extrapolates the time left from the speed it can actually observe — so it is right on a fast machine and a slow one, and says nothing at all until it has something true to say. (#1127)

- **Analytics you switched on would have stayed half-dead.** The backend half of the new opt-in analytics read its destination from an environment variable that nothing on your machine ever set — so in a shipped build it could never send anything, silently, no matter what you chose. Only the frontend half worked. The destination is now baked into the desktop shell at build time and handed to the backend when it starts, so "on" means on. Nothing else changes: it stays off until you opt in, builds from source still have no destination at all, and the property allowlist still decides what may leave. (#1123)

- **A dub that dies mid-transcription still guessed at the cause.** v0.3.20 taught it to check the crash report before blaming the ASR model — but it checked *instantly*, the moment the stream dropped, and the desktop shell needs about two seconds to notice the backend died and write that report. So it kept looking too early, finding nothing, and falling back to the same old guess ("Likely ASR backend failed to load") even when the backend had in fact just crashed. It now waits for the shell to catch up, so you get the real cause — exit code and error output — instead of a guess. (#1119)

## [0.3.21] — 2026-07-12

The memory release. The reason the app kept saying "Can't reach the local backend" on 16 GB machines was never really the network — the backend was quietly running out of memory and getting killed. This release fixes that at the source: the models it holds now get out of each other's way. Plus the uninstaller and factory reset grew into a proper Settings → Storage pair.

### Added

- **Factory reset grew up: Settings → Storage → "Reset & remove".** It used to do exactly one thing — clear your UI preferences — while the only other option was deleting everything and starting over. Between "forget my theme" and "wipe the machine" sat every reset people actually needed. Now there are four one-click tiers — **UI preferences**, **all settings**, **downloaded assets & models**, and **everything VoiceStudio did** — plus a per-item checklist if you want to drop just the model weights, just a wedged sidecar engine, or just the history. Every option shows its **real size on disk before you commit**, and the number on the button is exactly what gets freed. Deleting voices, projects or audio asks you to type `DELETE`; nothing irreversible happens on a single click. "Everything" deliberately stops short of the Python environment, so you land on a working first-run screen rather than a rebuild — the app stops its engine, deletes, and starts it again for you. On macOS and Linux the model cache is the **shared** Hugging Face cache, so it's its own checkbox and says so; on Windows and portable installs it's VoiceStudio's own, and the app doesn't pretend otherwise.

- **The Storage panels got a design.** "Remove all data" and "Reset & remove" listed folders as a flat run of text, so a 7.5 GB model cache and a 391-byte config file carried exactly the same visual weight — the one thing you actually wanted to see (where the space went) was the one thing you couldn't. Every row now has an icon, a dimmed path, and a **proportional bar showing its share of what will be freed**, so the big one looks big. The shared Hugging Face cache is promoted out of the confirm dialog into its own "Optional" row with a checkbox, so ticking it moves the running total **in front of you** instead of springing a different number on you at the point of no return, and the dialog now lists exactly what is about to go.

### Fixed

- **Switching TTS engines no longer stacks their models in memory.** Using a second engine in a session (or a per-request engine override) loaded its model *on top of* the first one's, because the VoiceStudio core model and the other engines live in two separate caches that never coordinated — measured on a 16 GB M2, an `omnivoice` → `mlx-audio` switch left the machine holding both (footprint 3.9 GB → 4.3 GB, the ~2.8 GB core never freed). That accumulation is a direct contributor to the memory pressure behind the "Can't reach the local backend" OOM deaths. Now only one TTS engine's model stays resident: resolving an engine hands back every *other* resident engine first (the same `omnivoice → mlx-audio` switch now drops to ~1.5 GB). Steady-state single-engine use is unaffected; an A/B switch pays a re-load on the way back (~8 s for the VoiceStudio core, ~1–2 s for the lighter engines). Opt out with `OMNIVOICE_SINGLE_ENGINE_RESIDENT=0` if you have RAM to keep several warm. Two underlying leaks are fixed as part of this: every in-process TTS engine's `unload()` now actually frees its model and empties the device cache (previously all but VoiceStudio were silent no-ops), and `faster-whisper`'s `unload()` cleared the wrong attribute so its model was never released.

- **The backend no longer sits on ~2 GB of idle dictation model — the real reason it was being killed on 16 GB Macs.** Four reports of *"Can't reach the local VoiceStudio backend"* (#1076, #1092, #1093, #1101) all died at the same moment: during a generate, on a 16 GB machine. Measuring it showed the generate was never the problem — it costs about 116 MB. The problem was the **baseline**: the backend sat at **~6.2 GB even while idle**. The TTS model has always been unloaded after an idle timeout, but the speech-recognition model used for dictation never was — so once you dictated a single time, ~2 GB stayed resident for as long as the app ran. On a 16 GB Mac, that plus the app, macOS, and your other programs is enough for the system to run out of memory and kill the backend, which surfaced as the "can't reach the backend" error. Dictation's model now gets the same idle release the TTS model already had, handing that memory back. The only cost is a ~1.4-second re-warm on your next dictation after a long pause, and a live dictation session is pinned so nothing is ever unloaded mid-sentence.

- **Folder sizes under 1 KB displayed as "0 KB".** The uninstall panel's `391 B` config folder rendered as `0 KB` — which reads as "nothing here" for a folder that very much exists. The Storage panels now share one byte formatter that can say `391 B`.

- **Some styling silently did nothing.** A handful of components referenced CSS custom properties that were never defined (`--chrome-fg-subtle`, `--chrome-bg-raised`, `--color-warning`). An undefined `var()` makes the whole declaration invalid, so the browser drops it and the element quietly inherits — the dimmed folder paths in the Storage panels weren't dimmed at all. Fixed in those panels, and a new guard (`frontend/src/test/cssTokens.test.js`) fails on any bare `var(--token)` in JSX that isn't defined in a stylesheet or documented as runtime-injected, so a typo can't ship as invisible styling again.

- **Uninstalling now removes the saved-environment file it used to leave behind.** VoiceStudio keeps a small `~/.config/omnivoice/env` file (the model-cache location you chose, and any saved Hugging Face token). Every uninstall path — the in-app "Remove all data", `scripts/uninstall.sh`, and `scripts/uninstall.ps1` — walked right past it, so a later reinstall silently picked the *old* file back up and redirected its downloads to a location you may have long since deleted. All three now list and remove it (it's the same `~/.config/omnivoice` path on every OS, Windows included), and the per-platform tables in `docs/install/uninstall.md` document it.

- **Disk usage now counts installed sidecar engines instead of hiding them.** Settings → Storage measured engine venvs in `backend/engines` — the built-in engine *code*, which has no venvs — so a multi-GB IndexTTS-2 install (which actually lives in `DATA_DIR/engines/<id>`) was invisible in the engine row and quietly rolled into the data dir's "other" subtotal. The report now points at the real install location and sizes the **whole** install (venv + checkout + weights), counted once, so "IndexTTS-2 — 6.2 GB" shows up where you'd look for it.

## [0.3.20] — 2026-07-12

The follow-through release. v0.3.19 promised that "Can't reach the local VoiceStudio backend" would stop firing while the backend was merely restarting — and then a user hit it anyway, on 0.3.19, because the fix had a race in it. That's closed properly here. Uninstalling also stopped being a thing only maintainers could do: it's now a button in the app, where the person who asked for it can actually reach it.

### Added

- **Uninstall is now in the app: Settings → Storage → "Remove all data".** The v0.3.19 uninstaller was a *script* — which never reached the people who needed it, since anyone who installed the .dmg / .msi / AppImage has no repo to run it from (exactly the case in #1089). The app now lists every folder this install owns with its real size, deletes them behind a typed confirmation, and quits. The **downloaded model weights are a separate, opt-in checkbox**, because that's the standard Hugging Face cache shared with other AI tools on your machine — removing it can delete models VoiceStudio never downloaded. Custom and portable install locations are honored, and nothing outside VoiceStudio's own folders can be touched. The scripts now also ship as **release assets**, so you can clean up without launching the app at all. (#1089)

### Fixed

- **"Can't reach the local VoiceStudio backend" could still fire on 0.3.19 — the fix had a hole.** The app asks the desktop shell whether a start/restart is in progress before showing that error, but the shell learns of a dead backend from a **2-second poll**: when the backend dies mid-generation, the supervisor needs a moment to notice it, record the crash, and flip its state to "restarting". The app was asking **once**, ~3 seconds in — often still hearing "everything's fine" — and dead-ending on the generic toast anyway. A failed connection *contradicts* "everything's fine", so that answer is now treated as stale rather than authoritative: the app keeps retrying briefly, letting the shell catch up, which turns the failure into the "backend is restarting — hang tight" banner (and gives the crash report time to be written, so you get the real cause instead of a guess). A shell that has genuinely given up, or no shell at all, still errors immediately. (#1101)

- **The uninstaller was leaving the backend's log folder behind on Linux and Windows.** It cleaned the app-data, config, and Python-env folders but missed where the backend actually writes `backend.log` / `backend_err.log` — `~/.local/state/OmniVoice` on Linux and `%LOCALAPPDATA%\OmniVoice\Logs` on Windows. Both the scripts and the documented path lists now cover them. (#1089)

## [0.3.19] — 2026-07-12

The honesty release. Every error in here was already *technically* true and practically useless — so this round went after the lies the app tells when something goes wrong. "Can't reach the local VoiceStudio backend" no longer fires while the backend is simply still starting; a dead Hugging Face mirror no longer strands the setup wizard with advice it can't follow; and a dub that dies mid-transcription now names the actual cause instead of guessing at it. Alongside that: generated speech starts playing on the *first* chunk instead of the last, and there's finally a real uninstaller.

### Added

- **Generated speech starts playing on the first chunk, instead of after the last one.** Long text is synthesized in chunks, but you used to sit through the entire render before hearing anything. The Studio now streams the preview: audio begins the moment the first chunk is ready and the rest arrives as it renders, so a long passage is audible in about the time the first sentence takes. The take saved to your history is **byte-identical** to the non-streaming render — streaming is a delivery channel, not a different synthesis path — and if a stream fails mid-flight the app falls back to the classic whole-file flow with nothing half-written to disk. (#1088)

- **A clean uninstaller + a straight answer to "where's my data?"** VoiceStudio is fully local, so removing it is just deleting the folders it wrote — but until now users had to guess which ones. New `scripts/uninstall.sh` (macOS/Linux) and `scripts/uninstall.ps1` (Windows) find every VoiceStudio folder — app data, the multi-GB managed Python env, config, logs, and (separately, because it's shared) the Hugging Face model cache — print each with its size as a **dry-run first**, and delete only on `--yes`. They honor your custom locations (`OMNIVOICE_DATA_DIR`, `HF_HOME`, portable mode) and never touch the app binary. The complete per-platform path list lives in the new `docs/install/uninstall.md`, linked from the README FAQ, SUPPORT, and troubleshooting. (#1089)

### Fixed

- **A dub that dies mid-transcription now says what actually happened instead of guessing.** "Transcribe stream dropped before emitting any segments. Likely ASR backend failed to load" was a *guess* — and usually the wrong one. The backend is contract-bound to emit a terminal event on every stream even when it fails, so a stream that simply goes silent means the backend **process died underneath it** — on smaller GPUs, almost always a native out-of-memory abort while loading the ASR model on top of a still-resident TTS model. The app now consults the desktop shell's crash forensics and tells you that: the exit code, when it happened, a one-click "View crash details" with the captured error output, and the actual next step (free VRAM / pick a smaller ASR model) rather than "check the backend log". With no crash recorded, the original message still stands. (#1062)

- **"Can't reach the local VoiceStudio backend" stopped crying wolf during startups and restarts.** A real backend start or auto-restart takes 10–20+ seconds (Python spawn plus the PyTorch import), but the app's transport retry only bridged ~3 seconds — every click inside that window dead-ended with the scary toast, over and over, even though the backend healed itself moments later. The app now asks the desktop shell whether a start/restart is actually in progress and simply waits for it (up to the shell's own 2-minute restart budget), and shows a single "backend is restarting — hang tight" banner with a "back — carrying on" confirmation — the reconnecting affordance the supervisor has promised since #567. A truly dead backend (or a non-desktop deployment) still errors promptly, and the crash notice keeps telling the honest story.

- **A dead Hugging Face mirror can no longer strand the first-run wizard.** When a model download failed because the *configured* mirror was unreachable, the error pointed at Settings — which first-run users can't open (the wizard gates the studio) — and falsely claimed the mirror setting only applies after a restart (downloads actually pick it up per call, immediately). Now the wizard shows the mirror quick-pick (including "Hugging Face (official)") right next to the failed download and retries it the moment you switch; the corrected hint says retry-first, restart only if it still fails. Two backend holes in the same flow are closed too: switching endpoints clears the "failed recently" retry cooldown (no more 429 on the immediate retry), and clearing to official also removes the legacy `hf_endpoint` pref, which used to silently keep the dead mirror in effect.

### Changed

- **The first-run wizard shows the app version in its masthead**, next to the VoiceStudio title — so setup-time screenshots and bug reports identify the build at a glance (the install splash already did).

- **Repo root decluttered.** Retired the finished planning archives (`.planning/`, `specs/`), the pre-React design mockups (`design/`), the legacy research dir (`research/`), and stale third-party agent rules (`.agents/`) — ~110 files of process noise gone; everything stays in git history, and the four load-bearing engine decision docs moved to `docs/adr/`. Contributor-facing only; the app is unchanged.

## [0.3.18] — 2026-07-12

The self-sufficiency release. Two long-standing "works on my network / works after four terminal commands" walls came down: model downloads now find a reachable Hugging Face endpoint on their own (no more restricted-network first-run dead-ends), and IndexTTS-2 — previously the only engine that demanded a manual clone-venv-install ritual — installs itself with one click. Under the hood, a test-debt sweep hardened the suite that guards all of it.

### Added

- **IndexTTS-2 installs itself now — one click in Settings → Engines.** The emotion-controlled cloning engine used to demand four terminal steps (clone the repo, create a venv, `uv pip install`, set an environment variable); the row now has an Install button that does all of it — source fetch (git, with a no-git tarball fallback), an isolated venv that keeps its `transformers<5` away from the app, the ~6 GB model weights (via your configured/auto-selected Hugging Face endpoint), and configuration — with step-by-step progress, a disk-space check before anything is written, and resumable repair if anything is interrupted. The engine is usable the moment the job finishes, no restart; existing manual installs are detected and left untouched, and the manual steps remain as a collapsible fallback. The provisioner is parametrized so future sidecar engines (MOSS-v1.5, dots.tts, Confucius4) can reuse it. (#1083)

- **Model downloads now find a reachable Hugging Face endpoint on their own.** On networks where huggingface.co is blocked or slow (the class of first-run dead-ends behind #984), the app quietly probes the official endpoint and the hf-mirror.com community mirror, picks whichever actually works, remembers the choice, and re-checks only when a download fails or the pick goes stale — so a restricted-network first run reaches a working voice instead of a wall of connection errors. Anyone who already set a mirror (env var, pref, or Settings) stays exactly where they pointed: explicit choices are never auto-switched, and Settings → Models → Hugging Face mirror now shows the automatic pick with its measured latency plus a "Test again" button. Probes only touch the two download hosts — no geo-IP, no telemetry — and every download stays checksum-verified by `huggingface_hub` regardless of endpoint. (#1082)

### Fixed

- **Concurrent settings writes can no longer drop each other.** Two parts of the app saving preferences at the same moment (say, an engine install finishing while you change a setting) could silently lose whichever save landed first; preference writes are now serialized, with a regression test. Found and fixed as part of the IndexTTS-2 installer work. (#1083)

### CI

- **Test-suite debt sweep.** Exports coverage (26 new tests, which caught two real router bugs), fp16 default-dtype leak instrumentation, and a suite-order pollution class root-caused at its source — the checks that guard every release got stricter. (#1081)

## [0.3.17] — 2026-07-11

The polish release. The dubbing workspace can no longer trap you — an interrupted dub session used to relaunch into an eternal spinner that even reinstalling couldn't clear (thank you @nanai97 for the screenshot that cracked it). A 58-finding audit of every Settings panel got fixed end to end, **FFmpeg and yt-dlp stopped being your problem** (the app provisions its own, with a new Audio tools panel when you want control), the Engines and Models pages went compact and tabbed, the launcher stopped trusting half-dead backends, and the app finally opens at 100% scale.

### Added

- **FFmpeg, FFprobe, and yt-dlp stopped being your problem.** The setup wizard no longer lists them as system requirements with "brew install" homework — the app provisions them itself: shipped installs already bundle them, and when nothing is found the backend downloads its own checksum-pinned static build in the background, showing a single actionable card only if that fails. A new **Settings → Audio tools** panel gives back the control: per-tool version and origin (App package / Bundled / System / Custom), update / use-system / choose-file / restore-bundled — and **one-click yt-dlp updates** that survive app upgrades, because video-site support changes faster than releases. Install docs updated to match. (#1071)

- **The Engines and Models pages got compact and tabbed.** Engines is now one section with TTS / ASR / LLM tabs; every engine is a strict two-line, fixed-height row with truncated text and aligned status / GPU / isolation / action columns, so the whole engine list fits one screen — details like "Why unavailable?" expand below the row instead of stretching it. Models rows tightened the same way. (#1072)

- **The Engines and Models pages got a full readability-and-features pass.** Every engine row now carries a small identity mark and honest capability badges (voice cloning, device routing with the reason on hover, sidecar isolation), and engines that are ready-but-have-advice finally say so — upgrade hints used to be dropped before reaching the UI. The model store gains a filter, disk-space context next to downloads, "in memory — safe to unload" indicators, copyable setup snippets for opt-in engines, and empty states that tell you what to do next. (#1058)

### Fixed

- **The app no longer attaches to a "zombie" backend that looks alive but fails everything.** If a backend process survived while its install was replaced or deleted underneath it, it kept answering health checks from memory — so the next launch attached to it and every real request failed with a confusing access-control error. The launcher now runs a deeper probe (an endpoint that actually touches the database) before attaching, and replaces any backend that fails it. The local dev/test scripts also now terminate running instances before wiping data, which is how this state was produced. (#1077)

- **The app opens at 100% scale by default.** New installs rendered everything at 130% zoom, which read as oversized on typical displays. Fresh sessions now start at native size; if you already picked a scale in Settings → Appearance, your choice is kept. (#1074)

- **The app no longer relaunches into a dead "generating" dub session — the blank-pane-and-spinner trap.** The saved dub session was restoring its in-flight state verbatim: quit (or crash) while a dub was generating and every subsequent launch waited forever for work that died with the process — and reinstalling couldn't clear it. Interrupted sessions now reopen on the segment editor with all your work intact (or the upload screen if nothing was transcribed yet). Thanks to @nanai97 for the screenshot that told the whole story. (#1067)

- **A 58-finding audit of every Settings panel, fixed end to end.** Highlights: the About page linked to the wrong project's GitHub; Arabic rendered left-to-right (RTL wiring was missing); a saved proxy could never be cleared after a reload; the HF-mirror and refinement panels vanished entirely when the backend was down; "Test now" on the HF token served five-minute-old cached results; factory reset only cleared part of what it promised; pronunciation previews ignored language-scoped entries; the hotkey recorder swallowed invalid presses in silence; Settings search could strand you with an empty sidebar — plus first component tests for previously untested panels, full i18n for five all-English panels, accessible names across inputs, confirmed destructive actions, deep links instead of dead-end advice, temp-file reclaim, and log-sharing workflows. (#1059, #1060, #1061, #1063, #1064)

## [0.3.16] — 2026-07-11

The quality release. Three long-standing frictions got structural fixes: **regenerating no longer destroys good takes** (a takes rail with starring and restore), **audiobooks stop redoing finished work** (per-sentence caching — edit one line, re-render one line; crashes resume where they stopped), and **dub translations stay consistent and fit their timeline** (auto-glossary + a naturalness pass, plus fit prediction before any GPU time is spent). Under the hood, every text path now speaks numbers, times, and abbreviations correctly, the VoxCPM2 engine gained upstream-alignment guards, and a Windows first-run breaker — model downloads completing but the cache ending up with broken file links — now self-heals automatically. Thank you @dmnobunaga for the razor-sharp diagnosis on that last one.

### Fixed

- **Windows: model downloads that finished but wouldn't load now repair themselves.** On machines without Developer Mode, the model cache could end up with all its multi-gigabyte files downloaded but the snapshot's file links broken — and the app reported a misleading "does not appear to have a file named model.safetensors". The app now detects the broken links on load failure, restores just the missing pieces (reusing everything already downloaded, and falling back to real file copies where links can't be trusted), and retries once; if repair is impossible, the error finally names the actual cache folder to delete. Root-caused in the wild by @dmnobunaga — thank you. (#1056)

- **VoxCPM2: cloning reference clips are now conditioned, and outputs lose their silent tails.** Reference audio used to reach the model completely raw; it now gets edge-silence trimming and a 30-second cap (fail-open — short clean clips pass through untouched), and generated audio gets a trailing-silence trim. The install hint also moved to `voxcpm>=2.0.3`, which carries an important Apple-Silicon audio-quality fix — older installs keep working and see an upgrade hint in the logs. (#1055)

- **Streaming TTS requests without an `emo_alpha` field no longer crash.** A minimal `/ws/tts` request hit a `KeyError` and returned an error frame instead of audio — found while giving that route its first tests. (#1054)

- **Long generations no longer risk a multi-gigabyte memory spike while being watermarked.** The invisible watermark (on by default) pushed the entire waveform through AudioSeal in a single call, and its memory use grows with audio length — a multi-minute generation demanded a single ~2 GB allocation, enough to fail outright on a 16 GB machine already holding a model ("DefaultCPUAllocator: not enough memory"). Watermark embedding — and the Verify-audio detector, which had the same flaw with uploaded files — now processes audio in ~30-second chunks, so peak memory stays flat no matter how long the audio is. Detection also got sharper for spliced files: it now reports the strongest chunk instead of a whole-file average. (#1045)

- **The ⊕ Insert token list no longer climbs out of the viewport.** In the voice-clone script panel, the insert popover (expression tags, CMU phoneme chips) always opened *upward* from the textarea — and since that input sits at the very top of the panel, the list disappeared past the top of the window with no way to see or scroll it. It now opens below the input, where there's always room. (owner-reported)

### Added

- **Edit one sentence, re-render one sentence.** Audiobook and Stories renders now cache every synthesized sentence individually (content-addressed, under the existing chapter cache): fixing a single line in a chapter reuses all the untouched audio, and an interrupted render — crash, quit, power loss — resumes from the sentences that already finished instead of redoing the whole chapter. One byte cap bounds both cache layers, and chapter caches from released versions keep working. (#1048)

- **Numbers, times, and abbreviations are spoken correctly in every engine.** A conservative normalization pass now runs before TTS everywhere (Studio, dubbing, audiobooks): "3:30" is read as a time, "2" as "two" (29 languages), "Dr." as "Doctor" — while stray control characters and markup remnants that trigger engine hallucinations are stripped. Deliberately cautious: when a rewrite could be wrong, the text is left alone, and your pronunciation-dictionary entries always have the final say. Toggleable (`text_normalization_enabled`, default on). The OpenAI-compatible API, streaming TTS, and the batch queue run the same pass, so every door into the engines speaks text identically. (#1049, #1054)

- **Dub translations stay consistent and sound natural (LLM engine).** Before translating, one pass over the whole transcript builds a terminology glossary (your manual glossary entries always win) that rides along on every segment, so names and terms stop drifting mid-video. After each segment's direct translation, an optional reflect pass critiques and rewrites stiff lines into natural spoken dialogue — any failure silently keeps the direct translation. Both toggleable in the Dub tab; the reflect toggle states its 3-calls-per-segment cost. (#1050)

- **The Dub tab now predicts which lines won't fit — before wasting GPU time on them.** After translation, each segment gets a duration estimate (self-calibrating to your engine and language from the segments already rendered) and a "Tight fit" or "Won't fit +Ns" badge when the dubbed audio can't match the timeline even with speed-up. An opt-in "Suggest shorter lines" option asks the LLM for a meaning-preserving shorter rewrite you can apply per segment — never applied automatically. (#1051)

- **Generation takes: star the good ones, restore any of them.** Regenerating no longer means losing the previous result — recent takes appear in the workspace history with replay, star/unstar, and one-click restore as the active output. History is now capped (Settings → Storage, default 200 takes): the oldest unstarred takes are pruned, starred ones are kept forever, and an audio file is only deleted when nothing else references it. (#1052)

- **A persistent mini-player for all the audio that used to play "invisibly".** Generated output, voice-profile and dub-segment previews, story lines, Gallery voices, and Projects renders all played through a bare audio pipe — no waveform, no seek, no time, and (until v0.3.15's stop pill) no way to stop them. A slim player bar now docks above the Logs footer whenever such audio plays, on every page: live waveform (decoded once from the audio already in memory — nothing is re-fetched), click/drag/keyboard seek, play/pause, elapsed/total time, what's-playing label, and a stop button. It replaces the stop-only pill, and because it's part of the app's layout rather than a floating overlay, the pill's "covers the Production Overrides row at 1440×900" overlap class can't come back. Stories line previews also route through it — which makes them stoppable *and* fixes them being silent on the macOS/Linux desktop builds (their old playback path used blob: URLs, which WebKit refuses to play). (no issue — owner request following #1032's stop-pill band-aid)

## [0.3.15] — 2026-07-10

The cold-start release. Three "why is this broken on my machine" mysteries got solved at their roots: **first generations stop dying at 300 seconds** (the timeout was counting the model download as generation time — @moduvoice measured it on a Tesla T4: 0% GPU for the full window), **updates stop deleting engines you installed yourself** (the updater's dependency sync removed anything not in the app's lockfile — including things our own UI told you to install), and **the "slower than v0.3.5" regression is found and fixed** (clone profiles without a transcript were silently re-running a full Whisper transcription on every single generate). Also: Clear History is back, auto-played audio is finally stoppable, @stronghamjji hardened the dub pipeline against wedged transcribes, and @shakib30's community Colab notebook is now the linked no-GPU path. Thank you all.

### Added

- **Agent Skills: `npx skills add debpalash/omnivoice-studio`.** Two installable [skills](https://skills.sh) now ship in the repo — `omnivoice` teaches any AI agent (Claude Code, Cursor, Codex, …) to speak and transcribe through your local install via the OpenAI-compatible API, including your cloned voices; `oss-maintainer` packages the maintainer methodology this project is run with.

### Fixed

- **A fresh install's first generation no longer dies at 300 seconds while the model is still downloading.** The generate timeout was one clock around everything — including the engine's lazy multi-GB weight download on a cold start — so first requests burned the whole budget on the download (0% GPU the entire time, as a contributor's Tesla T4 verification measured) and failed with a misleading "too heavy for the available compute" error. Model loading now runs first under its own, much larger budget; the generate clock starts only once the engine is warm. A genuinely stalled download gets a new error that says so and points at Settings → Models. (#1033, #1037, evidence from #1014)
- **The OpenAI-compatible speech endpoint stops silently discarding quality settings.** `POST /v1/audio/speech` accepted `num_step` and `guidance_scale` in the request body with a 200 OK — and dropped them without a word, so API callers couldn't reach the model's documented quality preset (`num_step: 32`). Both are now declared, validated, and passed through to the engine, matching the native `/generate` endpoint. Caught by a contributor's measured Tesla T4 verification pass. (#1014)
- **Updating no longer uninstalls engines you added yourself.** Optional engines installed with pip into the app's environment (VoxCPM2, KittenTTS — exactly what Settings → Engines' own install hints say to do) were silently deleted by every app update, because the update's dependency sync removed anything not in the app's lockfile. Routine updates now leave your additions alone; the repair path ("Clean & Retry") still restores the exact known-good state, since a broken environment is sometimes *caused* by an extra package. (#1029)
- **Voice cloning stops re-transcribing the same reference clip on every generate.** Since v0.3.6, a profile saved without a transcript (the default) triggered a full ASR model load *plus* a transcription of the reference on every single synthesis — the "TTS got much slower than v0.3.5, same settings" regression. The first auto-transcription is now saved onto the profile, and repeated ad-hoc uploads of the same clip reuse a content-keyed transcript cache — so the cost is paid once, not per request. A transcript you typed yourself is never overwritten. (#1032)
- **The Clear History button is back.** The workspace redesign moved generation history into the right-side panels but dropped the old sidebar's clear-all control, leaving one-by-one deletion as the only way to empty a long history. Both the Voice and Dub history panels now have a Clear History button (with a confirmation), scoped to that workspace's history. (#1032)
- **The audio that auto-plays after a render can finally be stopped anywhere.** The finished-render playback has no on-screen player, and the only stop control lived in the Voice workspace's action bar — audio started from the Dub workspace, a profile preview, or after navigating away simply played to the end. A stop button now appears above the status area whenever such playback is active, on every page. The existing Settings → Appearance "Auto-play preview" toggle now also governs this playback, as its description always promised. (#1032)

## [0.3.14] — 2026-07-09

A fast follow to v0.3.13: **every engine family now has a visible picker.** Settings → Engines showed only a TTS table, with the ASR and LLM pickers hidden behind a low-discoverability tab — so the 10 transcription engines (including the new OpenAI-compatible backend) looked unswitchable without env vars. Now all three families get their own table. Also in: the Linux AppImage's white-screen auto-workaround now checks the WebKitGTK it actually ships (not whatever your system reports), and installing to a different drive on Windows is properly documented.

### Added

- **ASR engines get the same Settings picker TTS has.** Settings → Engines now shows a visible picker table per family — TTS, ASR, and LLM — instead of a single TTS-titled table with the other families tucked behind a tab (README even promised a Settings ASR picker that didn't exist). The OpenAI-compatible backend and the 9 local ASR engines become selectable with one click, no env vars needed; an explicit `OMNIVOICE_ASR_BACKEND` still wins over the Settings pick, so pinned setups behave exactly as before. (no issue — UX gap found during #877)

### Fixed

- **The Linux AppImage's white-screen auto-workaround now checks the right WebKitGTK.** The launcher decided whether to apply the compositing workaround by asking the *system's* `pkg-config` — but the version that actually runs is the *bundled* one, which the AppImage prioritizes. On any machine where the two diverge (e.g. building from source with newer dev packages installed), the detection read the wrong number and could skip a workaround the running library needed. The build now stamps the bundled version into the AppImage at package time, and the launcher reads that stamp — correct by construction. The launcher's shell tests also now run in CI, which they previously never did. (#961 follow-up)

### Docs

- **Windows: installing to a different drive is documented** — the wizard's directory picker works for any local drive; mapped network drives are a Windows Installer limitation (not installable-to by design); and the big data (models/voices) moves independently via Settings → Storage or Portable mode. (#938)

## [0.3.13] — 2026-07-09

The community-fixes release. Two contributors didn't just report bugs — they diagnosed them to the exact line and submitted the fixes that shipped: **voice cloning on mlx-audio's CSM model works for the first time**, and **macOS live recording finally gets its microphone permission prompt** (both @MahdiHedhli). A third reporter's A/B analysis fixed **cross-language dubs speaking the wrong language**. On top of that: a backend shutdown race that produced confusing crash-on-quit reports is fixed, the Linux AppImage stops shipping a stale WebKitGTK that white-screened current distros, and a new OpenAI-compatible transcription backend opens a path to Qwen3-ASR today. Thank you to everyone who filed, diagnosed, and contributed — this release is mostly yours.

### Added

- **A path to Qwen3-ASR today: generic OpenAI-compatible transcription.** The direct integration is still blocked on `transformers>=5.13` stabilizing upstream, but a community member proposed splitting the work — add a backend that talks to any OpenAI-compatible transcription server right now. Point VoiceStudio at a self-hosted Qwen3-ASR/FunASR/SenseVoice server, or OpenAI's own API, configured in Settings → Models. No install; audio does leave your machine to whichever server you configure, unlike every other ASR engine. (#877)

### Fixed

- **The Linux AppImage no longer white-screens on current distros with a healthy system WebKitGTK.** The release build ran on an older CI base image, and the resulting AppImage bundles whatever `libwebkit2gtk` that image's apt repos resolve — which the AppImage's own `LD_LIBRARY_PATH` then prioritizes over your system's newer, healthy copy at runtime. A from-source build (which links straight against your system library) worked fine on the exact same machine where the shipped AppImage didn't — that split was the tell. Bumped the release build to a current Ubuntu LTS. Raises the AppImage's minimum host to glibc 2.39 (Ubuntu 24.04+); no reports from anyone on an older distro. (#961)
- **Backend shutdown no longer races a still-loading model, surfacing a confusing crash on restart.** Quitting the app while a model was still loading in the background let shutdown report itself "done" while a background thread was still mid-import; tearing the process down under that thread produced a misleading error (a generic transformers import-failure message, unrelated to the real cause) that looked like a real crash rather than a timing issue. All background tasks are now properly cancelled and awaited before shutdown proceeds. (#1000, likely the same class behind #941 and #979)
- **Cross-language dub no longer speaks the source-language reference line verbatim.** Auto-generated speaker clones pair an audio slice with the ASR segment's own text field, assuming the two agree — but ASR segment text and its timestamps routinely drift (a trailing word audible in the clip but missing from the text, or vice versa). A mismatched (reference audio, reference text) pair breaks zero-shot TTS prompt priming badly enough that the clone can emit the reference text itself instead of the target-language line it was asked to speak. Each reference clip is now re-transcribed after it's written, so the pair matches by construction — reported with an exceptionally clear root-cause diagnosis and a working A/B repro. (#1004)
- **Voice Gallery errors now say what actually went wrong.** "Use voice", "Preview", search, upload, save, delete, and trim in the Gallery all showed the same hardcoded guess ("the engine may be loading") on ANY failure — a 500, a validation error, a genuinely unrelated bug — discarding the real, already-clean backend error message in the process. Every one of those now shows the actual error.
- **Voice cloning on mlx-audio's CSM model no longer crashes with an opaque "list index out of range".** `MLXAudioBackend.generate()` read `voice`/`ref_audio`/`language`/`speed` from its kwargs but silently dropped `ref_text` — CSM only builds its cloning context when both `ref_audio` and `ref_text` are present, so cloning on this engine could never have worked as shipped. Reported with the exact root cause and a working fix. (#1012, #1013)
- **A dub segment's free-text style tags no longer 400 the segment preview.** A validator-safe instruct builder already keeps Studio and Clone generation from round-tripping a 400 on unsupported free-text (a preset's raw attrs, an old profile's stray descriptive phrase) — but the Dub tab's segment preview, and saving a profile from a clone or from history, built their instruct strings directly and skipped it. Same guard now applies everywhere an instruct string is sent. (#1010)
- **The dub editor's play button no longer sticks permanently disabled after an audio-decode hiccup.** When the initial WaveSurfer decode fails, the timeline falls back to loading pre-computed peaks — the waveform draws fine, but the button's enabled state only relied on the `ready` event firing again for that recovery load, which it didn't reliably do. Each fallback path now confirms readiness explicitly once it settles.
- **macOS: live recording finally works — the microphone permission prompt now actually appears.** The app never showed up in System Settings → Privacy & Security → Microphone because macOS never saw a legitimate request: Tauri enables Hardened Runtime by default, which blocks microphone hardware access unless the matching entitlement is in the signed bundle — and it wasn't. Diagnosed to the exact mechanism and fixed by a community contributor (@MahdiHedhli), who also corrected our initial mis-read of this as an upstream WebKit limitation. (#1013, #1016)
- **Quitting during a slow model load waits longer before giving up.** A post-merge code review of the shutdown-race fix flagged that its 3-second wait could still be outrun by a cold model import on a slow disk, reproducing the original confusing-crash-on-quit in rare cases. The wait is now 20 seconds — imperceptible on a normal quit (tasks finish or cancel in milliseconds), only felt in the exact case it protects. (#1020)

### Changed

- **Removed the donate heart from the nav rail.** Support VoiceStudio is still one click away from Settings and the Contact page.

### CI

- **The "flaky trio" is root-caused and neutralized.** Three tests failed intermittently on CI — never locally — across unrelated PRs, costing a re-run each time. Cause: a leaked half-precision torch default from some earlier test in CI's ordering (the giveaway: a failing assertion's observed value was exactly float16(0.1)). An autouse test-suite guard now resets the leak between tests and names the offending test in CI output when it fires. (#1021)

## [0.3.12] — 2026-07-08

A community-issue sweep — nineteen open reports triaged in one pass, most fixed same-day. The through-line: **your active engine selection is now honored everywhere** (dubbing, batch, and — new in this release — MLX-Audio's own curated models are finally selectable instead of always silently defaulting to Kokoro), **first-run stops dead-ending users on restricted networks or behind corporate TLS proxies**, and a run of sharp community diagnoses (a one-line ROCm index fix, a Windows-only focus-stealing bug, a genuine crash regression) got fixed largely because reporters did the hard diagnostic work themselves. Thank you.

### Added

- **MLX-Audio's other 6 curated models are finally selectable.** The engine multiplexes Kokoro, CSM, Qwen3-TTS, Dia, Chatterbox, MeloTTS, and OuteTTS, but there was no way anywhere in the UI or API to pick which one loads — downloading a model via Settings → Models did nothing, since the backend always defaulted to Kokoro regardless. Settings → Engines now shows a model picker on the mlx-audio row; switching takes effect immediately, no restart needed. (#981)

### Fixed

- **First-run no longer dead-ends behind restricted networks (e.g. China).** The system check probed hardcoded huggingface.co, and any failure locked the Continue button — users behind the Great Firewall were stuck on the very first screen, even when they had already configured a working mirror. The check now probes the Hugging Face endpoint actually in effect, an unreachable endpoint is a warning instead of a blocker (models already on disk keep working offline), and when huggingface.co is blocked but the hf-mirror.com community mirror answers, the wizard says so and offers a one-click mirror switch right on the check screen — no restart needed. (#984)
- **Installs behind a corporate or antivirus TLS-inspecting proxy no longer fail with a raw SSL error.** `SSLV3_ALERT_HANDSHAKE_FAILURE` happens when a proxy re-signs HTTPS traffic with a root CA your OS trusts but Python's bundled certificate list doesn't — a different failure mode from the network-blocking case above. VoiceStudio now trusts your OS's certificate store directly, which should resolve the handshake outright rather than just explain it better. (#976)
- **The loaded-models panel now says when a resident model is not your active engine.** Switching TTS engines keeps the previous model in VRAM (so switching back is instant) — but the panel showed it with no context, so "VoiceStudio TTS — 1.9 GB" after selecting VoxCPM2 looked like the selection was ignored. A field report confirmed the confusion. Resident-but-inactive models are now tagged "not active — safe to unload", and the API self-describes each entry's engine. (#985)
- **Voices no longer ship with a hidden echo.** Every non-raw synthesis was getting a small room reverb baked in by the mastering pre-stage — on top of whatever effect preset you chose, so even "Podcast" (which promises *no reverb*) had some, and Cinematic/Warm got it twice. A field report ("a lot of echo/reverb on some of the voices") led straight to it. The mastering stage is now highpass + compressor only; reverb happens only when a preset explicitly declares it. Also documented: cloned voices reproduce the reference clip's room acoustics — dry, close-mic references clone cleanest. (#986)
- **Your engine selection now actually applies to Dubbing and Batch TTS.** Both hardcoded VoiceStudio regardless of what was picked in Settings → Engines — pick VoxCPM2, dub anyway with VoiceStudio, no error. Both now resolve the active engine up front; an engine that can't clone from reference audio (KittenTTS, Sherpa-ONNX, Supertonic 3 — fixed preset voices only) fails the job immediately with a clear message naming which engines do support it, instead of silently substituting VoiceStudio or mis-cloning every speaker into one voice. Batch only requires cloning when a specific voice is pinned — an unpinned batch job runs on any engine. (#987)
- **AMD ROCm torch install no longer silently falls back to CPU.** A community member (Kaihui-AMD) diagnosed it precisely: the ROCm wheel index we pointed at tops out at PyTorch 2.5.1, but the app pins `torch==2.8.0` — the reinstall was unsatisfiable and silently kept the default CUDA build, which runs on CPU on an AMD GPU. Bumped the default index to one that actually carries the pinned version. (#972)
- **mlx-audio no longer crashes on unsupported languages.** Selecting a language like Dutch, Spanish, or Portuguese with mlx-audio's Kokoro model crashed with a raw, unreadable internal-details dump instead of a real error — the code was guessing an ISO language code by truncating the language name, which only worked by coincidence for a few languages. Unsupported languages now fail cleanly with a message naming what's actually supported, and no engine can leak a raw crash-internals dump into an error message again. (#977)
- **The voice-design panel no longer crashes on certain saved voice profiles.** A genuine regression: an earlier translation fix accidentally introduced a crash when a saved design profile's data was incomplete (possible from an older app version or a partial save). Fixed at every layer — the render no longer crashes, both places that restore saved data complete it first, and profiles can no longer be *saved* with incomplete data in the first place. (#983)
- **Windows: the dictation pill no longer steals focus.** Pressing the dictation shortcut activated the pill window, which meant the auto-paste landed back in VoiceStudio instead of whatever app you were dictating into, and the pill would get stuck on screen. Precisely diagnosed by a community reporter; fixed to match how this already worked on macOS. (#982)
- **The nemo-parakeet ASR engine's install hint no longer breaks your backend.** Following the in-app "pip install nemo_toolkit[asr]" instruction silently downgraded core packages your backend needs to start — the install reported success, and the breakage only showed up on the next restart. The hint now says plainly that this isn't safe to install into the shared environment. (#974)
- **A stuck generate now tells you the actual fix.** When a job times out from GPU/VRAM contention, the error explained why but never mentioned Flush/Unload — the one action that actually resolves it, and one the sibling ASR-timeout error already recommended. (#939)

### Changed

- **README and Linux docs no longer advertise a `.deb` package that isn't published.** `.deb` bundling is disabled in the release pipeline pending a tauri-cli fix; the docs now say so honestly instead of pointing at a file that was never in any release. (#961 investigation, #990)
- **Linux install docs mention `yt-dlp` as an optional prerequisite** — previously only surfaced via an in-app warning after the fact. (#973)
- **A benign Tauri startup warning no longer looks like an app problem.** On some Windows configurations, Tauri's own internal IPC fallback logs a warning that's fully harmless (it silently and successfully falls back to another transport) — it was spuriously flipping the Settings → Logs footer to show "1 warning" on every launch. Filtered out of the diagnostic capture. (#975)

## [0.3.11] — 2026-07-05

The multi-language release — dubbing into several languages at once is finally a mature, honest workflow: **"Generate N dubs" now translates each language before rendering it** (with visible per-language progress), **switching languages never destroys your work** (every track keeps its own text, subtitles, and audio cache), completed tracks always show their tabs, and dialogue stops starting seconds early because of footsteps — a community reporter's theory, confirmed exactly. Around it, a reliability sweep driven by same-day field reports: your LLM provider finally survives a restart, SOCKS-proxy users can synthesize again (installed models now load without touching the network at all), timeline boxes are visible on every WebView2 runtime, running from source works again — and when the backend crashes, **it now tells you the exit code and attaches the evidence to your bug report automatically**.

### Added

- **Backend crashes are now self-documenting.** When the local backend process dies (a native GPU abort, an out-of-memory kill), the app used to show only "Can't reach the backend" — undiagnosable without logs nobody sends. The launcher now records every unexpected backend death (exit code, how long it ran, the last 40 log lines), tells you honestly that it *crashed* and is restarting, offers a "View crash details" panel, attaches the evidence to in-app bug reports automatically (paths scrubbed), and stops silent crash-loops after 3 deaths in 10 minutes with the details on screen. Intentional shutdowns, restarts, and app quits are never misreported as crashes. (#969)
- **"Generate N dubs" now actually translates each language first.** Multi-language generation used to synthesize every track from whatever text was in the editor — so at most one of your N dubs was really in its language. The batch now runs translate → generate per language with a visible "Translating → Bengali (2/3)…" phase, skips (and reports) any language whose translation fails instead of rendering a wrong-language track, and your multi-language picks and export-track selection are saved with the project instead of vanishing on tab switch. (#957)
- **Switching dub languages no longer destroys your work — every track keeps its own text and audio.** Translations are now stored per language (switching the target swaps the editor text non-destructively; manual edits stay with their language), subtitles export each track's own text instead of N identical files, burned-in subs match their track, and the per-segment audio cache is keyed by language — "Regen changed" can no longer splice another language's audio into the track you're rebuilding, and staleness is tracked per track. Fully backward-compatible: existing projects and caches keep working; a pre-upgrade project's first "Regen changed" simply regenerates cleanly once. (#958)

### Fixed

- **Timeline segment boxes are visible on every WebView2 runtime.** The v0.3.10 flicker fix switched box colors to a newer CSS feature (`color-mix`) applied as an inline style — on WebView2 runtimes older than ~March 2023 (pinned enterprise/offline installs) that renders as *fully transparent*, turning "flickering boxes" into "no boxes at all" while looking perfect on up-to-date machines. Colors are now pre-blended in plain JavaScript to universally-supported `rgb()` values — pixel-identical on modern runtimes, theme-aware, and guarded by a test that fails if an engine-dependent color ever reaches the timeline again. (#968)
- **Dubbed dialogue stops starting seconds early because of footsteps.** Dialogue starts are snapped to the first detected sound — and a single 20 ms burst (footsteps, a door, a sigh) counted as "speech", with no limit on how far a start could jump, and the snap even ran on the raw mix when vocal separation had failed. Onsets now require sustained speech-like energy, long jumps are only allowed across genuinely silent spans (so the original fix for whisper's stretched starts keeps working), and snapping turns off entirely when vocals weren't separated. Credit to the community reporter whose "footsteps theory" was exactly right. (#967)
- **Completed dub tracks always show their video tabs.** Opening a project with a finished dubbed track hid the Original/track switcher until you re-selected the language — visibility was keyed to the language dropdown instead of the project's tracks, and restored projects couldn't set the language because the history database froze it at empty forever. Tabs now render from the tracks themselves, history keeps its language (existing projects heal without migration), restoring a project can no longer 404 the video preview, and track pills gained duration/timing tooltips plus an accurate now-playing indicator. (#956)
- **Running from source works again, and the install docs stop lying.** `bun run desktop-prod` broke when the frontend became a workspace (`bunx` could fetch the wrong "tauri" package from npm — fixed everywhere including CI); the Linux white-screen guidance now leads with the variable that actually fixes modern Ubuntu (`WEBKIT_DISABLE_DMABUF_RENDERER=1`, with the exact `EGL_BAD_PARAMETER` error quoted); Windows docs now state plainly that GPU acceleration is NVIDIA-only there; the Linux docs document the ROCm support that already shipped (the "planned follow-up" note was stale); and prerequisites are split installer-vs-source with git and curl included. (#964)
- **Your LLM provider now survives a restart.** Setting up Ollama (or any provider), testing it, and saving looked like it worked — then a restart forgot the selection: only the separate "Save & use for translation" button ever persisted it, and a leftover setting from the retired (≤0.3.7) translation panel could silently steal the choice back to "Custom" on every launch. An explicit save now activates the provider when none was chosen yet, the leftover legacy settings are migrated into the Custom provider once and removed, and the panel says "Saved — not yet used for translation" instead of staying silent when your edit isn't the active provider. (#965)
- **SOCKS-proxy users can synthesize again — and an installed model can never again be blocked by a broken network stack.** With a system-wide SOCKS proxy set, clicking Synthesize 500'd with a raw "socksio not installed" error: loading an already-downloaded model still constructed a network session first, which failed at creation. The app now ships SOCKS support (including in the packaged installers), resolves installed models **cache-first** (no network session when the files are already on disk — the local-first guarantee at the loader level), warms up at startup even when the online check fails, degrades LLM extras instead of crashing on proxy errors, and classifies the error with an actionable hint if it ever does surface. (#966)

## [0.3.10] — 2026-07-05

The listening release — nine fixes in twenty-four hours, almost all driven by your v0.3.9 field reports (several with same-day turnaround). The dubbing pipeline stops lying: **Cinematic and Autofit can no longer invent dialogue**, the **speaker count you set is honored on every path** (and auto-cloning stops fabricating voices from guessed labels), and the timeline stops flashing invisible on Windows. Audiobook chapters with pauses render again. And one fix everyone should want: **updating can no longer leave you secretly running the old version** — a leftover backend from a previous install holding the port is now detected and replaced at launch. Plus: the Dub tab's LLM engine finally runs on the provider you configured in Settings, history timestamps stop reading "20617d ago", and the Engines page can't crash under concurrent load.

### Fixed

- **Audiobook/Stories chapters with a `[pause]` no longer fail to render.** Pause spans were built as 1-D silence while every TTS engine returns 2-D audio, so the chapter concatenation crashed with `Tensors must have same number of dimensions` — any chapter containing a pause failed on every attempt (reported with a precise trace in #897). Silence now matches the rendered audio's shape at the source, and the chunk concatenator defensively normalizes mixed ranks (including honest mono→stereo broadcast) so no engine can re-trigger the class. (#953)
- **Cinematic and Autofit dubbing can no longer invent dialogue.** The refine and slot-fit passes accepted any non-empty LLM reply for Latin-script languages — hallucinated lines, refusals, or the critique itself could ship as the dub. Every reply is now checked against the original line (length window, target script, critique echo — tunable via `OMNIVOICE_REFINE_RATIO_MIN/MAX`), rejected output falls back to the literal translation with an `adapt-diverged`/`fit-diverged` marker, lines too short to honestly fill their slot skip LLM expansion entirely, and both passes pin `temperature=0.2` like the Fast path. (#950)
- **Dub timeline boxes can no longer flash invisible during playback.** On some Windows GPU/WebView2 driver combos the segment boxes under the video vanished and reappeared while playing (first reported in #373; the earlier fix was incomplete) — the timeline lane still animated a CSS transform every playback tick, keeping the translucent boxes on a composited layer that the driver mis-painted. Boxes are now positioned in pure layout with fully opaque theme-aware fills (pixel-identical colors), removing the glitch class on every platform. (#951)
- **The dub "Speakers" count now actually does something — on every path.** The hint only reached pyannote; the common fallbacks silently ignored it (the no-diarization heuristic was hardcoded to alternate two speakers, and the FunASR shortcut never consulted it). The heuristic now cycles the requested count, an explicit count routes through pyannote when available, every path that can only approximate (or must ignore) the setting says so in a visible warning, and the legacy endpoint + a new CLI `--speakers` flag accept it too. Auto voice-cloning also stops fabricating voices from guessed labels: reference slices under 1.5s are rejected, slices bordering another speaker's turn are avoided, and cloning is skipped with an honest warning when speaker labels came from the gap heuristic instead of real diarization. (#952)
- **Settings → Engines can no longer 500 under concurrent loads.** The lazy TTS/ASR engine registries held a *live* dictionary iterator open across each engine's `is_available()` probe while `list_backends()` ran in a FastAPI threadpool — so a second concurrent `/engines` request materializing a lazy engine entry (`self[key] = cls`) mutated the dict mid-iteration and crashed the request with `RuntimeError: dictionary changed size during iteration`. Both registries now snapshot their keys before iterating (atomic under the GIL), immune to a concurrent insert; regression-tested for TTS and ASR. (#940)
- **The Dub tab's LLM translation engine now runs on your configured LLM provider.** Picking "LLM (OpenAI-compatible)" silently required three hand-set environment variables even when a provider was already configured and tested in Settings → LLM Providers; it now resolves through a new "Dub translation" LLM skill (route it to any provider — remote or local — in Settings → LLM Skills, independently of Cinematic refinement), keeps the `TRANSLATE_*` env vars as a power-user override, bounds every call with the LLM timeout instead of the SDK's 600-second default, tells the Engine dropdown whether the engine is actually ready (and via which provider), and — when nothing is configured — returns a clear pointer to Settings → LLM Providers instead of a raw 401 per segment. (#944)
- **Timestamps no longer show "20617d ago" in OmniDrive/Projects.** The backend stores record times in Unix seconds while some views assumed milliseconds, so generation-history cards rendered as ~1970 ("20617d ago") and sorted last; every relative-time label (OmniDrive, sidebar history, dub projects, batch queue, transcriptions) now goes through one unit-tolerant formatter, and records missing a timestamp show "—" instead of an epoch age.
- **Updating can no longer leave you secretly running the old version.** If a backend from a previous version was still holding the port (an orphan that survived an update), the new app "attached" to it because it answered health checks — so every fix in the update appeared to change nothing (the reported "bound port blocked the newer version"). The launcher now compares the running backend's version against the app before attaching: same version attaches as before, a stale one is killed and the bundled backend is started in its place — on macOS, Windows, and Linux. (#947)

## [0.3.9] — 2026-07-04

The dictation release — and a deep reliability pass driven by live-testing the entire app. **Dictation is rebuilt end-to-end**: instant feedback with a live waveform, words that commit about half a second after you stop speaking, clean punctuation, and text insertion that never lies about success. **LLM providers get one-click connection testing** with real diagnostics and model discovery, in all 21 languages. The app now **always opens maximized**, bottom buttons **can't hide under the footer** at small window sizes, and a wave of "out of memory / can't reach the backend / stuck at preparing" reports were traced to their real causes and fixed — including the silent VRAM crash on 8 GB cards, dead-IPC startup hangs after a Windows BSOD, and misleading error labels. Intel-Mac support status is now stated honestly, Confucius4-TTS is validated end-to-end, and Parakeet — roughly 20× faster than the default transcriber on CPU — is unlocked for every machine.

### Added

- **Sponsor VoiceStudio.** A new `SPONSORS.md` (tiers, logo guidelines, how to sponsor), a README Sponsors section, and an in-app Sponsors area (Support page + a footer link) let people back the project — with a one-click "Become a sponsor" that opens a structured GitHub issue form, no account or token needed. Sponsorship is a thank-you, not a paywall: VoiceStudio stays free and AGPL-3.0. (#923, #924)
- **OpenAPI reference in Settings.** A new Settings → OpenAPI page embeds an interactive Scalar reference for VoiceStudio's local backend API, with a one-click footer button. Fully local — Scalar is bundled, not loaded from a CDN, and phones home to nothing. (#928)
- **Engine Self-test.** The Engines matrix gains a "Self-test" button for in-process TTS engines that runs a tiny real synthesis and reports duration + sample rate — proving an engine actually makes audio, not just imports — plus a copy-paste `export OMNIVOICE_*_DIR=…` setup line for opt-in engines right in the "Why unavailable?" panel. (#930)
- **One canonical HuggingFace-token store + incomplete-download visibility.** The Model Store token field now saves to and is cleared from the same encrypted store as Settings → Credentials (no more two-stores split), and a truncated model cache shows an "incomplete · N MB" state with one-click Repair and Delete instead of masquerading as "not installed". (#927)
- **Launchpad, reimagined as a deck of cards.** The seven feature cards now fan out with animated waveform faces in each card's accent color; hover or keyboard-focus any card and it comes forward while the rest tuck underneath, and the layout stays usable down to the minimum window size. (#904)
- **See exactly what VoiceStudio keeps on disk — and get warned before space runs out.** Settings → Storage shows real usage for the model cache (with your largest models), app data, engine environments and temp files, plus a free-space gauge and low-disk / near-full-volume warnings with one-click paths to open folders or reclaim space. (#906)
- **A "What's new" changelog reader in Settings → Updates.** The available update's real release notes now render in-app, alongside an offline changelog viewer and a one-time "what's new" note after each update. (#909)
- **Route each AI feature to its own LLM — or switch it off.** A new Settings → LLM Skills panel lists every LLM-powered capability (Cinematic/Autofit translation, slot fitting, glossary auto-extract, direction parsing, dictation cleanup) with a per-skill toggle and provider picker, so sensitive work can stay on a local model while heavier jobs use a remote one. Disabled skills fall back to the exact non-LLM behavior. (#912)
- **A small thank-you moment, done right.** After a successful export, dub, audiobook, or batch run, VoiceStudio may — rarely — show a friendly, dismissible note by the footer heart about supporting development: never more than once a session, at most every 7 days, never for brand-new users, with a permanent "don't ask again". The logs bar also gained an icon and the footer icons now share one size. (#898)

- **Dictation, rebuilt.** The dictation pill now shows a live waveform the moment the mic opens, streams words as you speak with real download/loading progress on first use, and finishes what you say in about half a second of silence instead of two-and-a-half. Transcripts come out properly capitalized and punctuated. Text insertion is now honest and safe: your clipboard is preserved and restored, failures show what to do (including a one-click jump to macOS Accessibility settings when permission is missing) instead of a false "Pasted", and Esc cancels cleanly at any point. The dictation model also pre-warms in the background after launch, so the first press of the hotkey no longer sits on a cold model load.

- **LLM Providers: one-click connection testing with real diagnostics.** The Test button in Settings → LLM Providers now measures round-trip latency and turns failures into plain-language guidance — bad key (401/403), wrong model or URL (404), rate-limited (429), or unreachable server — instead of a raw exception dump. A new "Fetch models" button lists every model your key can access so you pick from real names instead of guessing. The whole panel is now translated into all 21 languages, provider error messages never echo your API key, and the settings API gained full test coverage.

### Changed

- **A "Get in touch" page that actually guides you.** The Contact page is now clearly-labelled cards (report a bug, request a feature, get community help, support the project, report a security issue) with a sentence each on when to use them, instead of a flat link list. (#925)
- **Release titles are version-first.** GitHub's release-list sidebar truncates the title, so "VoiceStudio v0.3.8" hid the version; releases are now named "vX.Y.Z — VoiceStudio" so the version is always visible. (#922)
- **Launchpad feature cards now fill the window.** The seven cards (Voice Clone, Voice Design, Video Dubbing, Stories, Audiobook, Voice Gallery, Transcripts) span the full content width on a maximized display instead of a fixed ~780px fan, and reflow responsively (7→3→1 columns) down to the 900×600 minimum — driven by the shell's own width, keeping the animated card faces, hover/keyboard-focus raise, and reduced-motion fallback. (#915)
- **LLM Providers settings, de-confused.** The old inline "LLM endpoint" box in Translation is gone — LLM Providers is now the one place that owns it. Fields pinned by an environment variable are shown disabled with an explainer instead of silently reverting, the make-active button explains when a provider is env-pinned, and the Cloudflare Account ID is remembered and editable. (#907)
- **Intel Macs: honestly unsupported for the local backend.** PyTorch no longer ships Intel-Mac builds, so the backend cannot run there; instead of a cryptic dependency error, Intel users now get a clear explanation up front (with the remote-backend option), and the README/docs say so plainly. (#889, #891)

- **The app now always opens maximized (not fullscreen).** Window size and position are no longer carried over from the previous session — one manual resize used to make every later launch reopen at that smaller size, overriding the intended maximized default. Same behavior on macOS (zoomed window, not a fullscreen Space), Windows, and Linux.

### Fixed

- **Sherpa-ONNX "model not set" now reads as a setup problem, not out-of-memory.** Selecting the sherpa-onnx engine without `OMNIVOICE_SHERPA_MODEL` configured used to fail with a misleading "ran out of memory — press Flush" 500; it now names the exact variable, points at Settings → Engines, and the engine is marked unavailable-with-a-reason in the picker (with a copy-paste setup line) instead of selectable-but-broken. Generalized so any env-gated engine surfaces actionable setup guidance. (#919)
- **Cinematic & Autofit now actually run on every translation engine.** Picking Cinematic or Autofit on the default Argos engine (or NLLB) used to silently fall back to Fast with a success toast; it now runs the full LLM refine + fit pass, the Autofit fit pass is bounded by the same wall-clock budget as Cinematic, and provider errors are scrubbed of keys/user-ids. (#910)
- **Dictation no longer freezes on a slow or dead LLM.** Transcript refinement is now hard-bounded (default 4s): a placeholder key or unreachable endpoint falls back to clean unrefined text instead of stalling the paste ~51 seconds. The dictation model is genuinely pre-warmed and reused across sessions, REST transcription is polished like live dictation, and Settings flags a configured-but-failing LLM. (#911)
- **Model installs fail loudly, not silently.** Failed downloads keep their mirror-aware reason on the row with Retry/Dismiss instead of vanishing after a moment; installs check free disk space up front before overrunning it; in-progress installs get a Cancel button; and the HF-mirror setting only asks for a restart when it actually changed. (#908)
- **Engines settings, sharper and honest.** The Supertonic license "Accept" button works again (it was inert since it shipped), the engine matrix refreshes the instant you pick an engine, picking a GPU engine that lands on CPU now warns you with the reason, CPU-only engines stop being mislabelled "CPU fallback", and an in-process "Test engine" pass reads as a dependency check instead of a fake "0 ms" latency. (#905)
- **Updates can no longer cost you data.** Before any database migration runs on first launch of a new version, the database is snapshotted next to itself (newest three kept), and a failed migration stops with the backup path named instead of silently running on a half-upgraded database; the environment self-heal now verifies it's actually broken before rebuilding. (#909)
- **CUDA transcription now works on packaged NVIDIA installs — the cuDNN 8 compat libraries install automatically at launch.** The install step only existed in the dev-loop `scripts/setup.py`, which isn't bundled into the packaged app, so real installs never got the libs and WhisperX / faster-whisper failed with `Could not locate cudnn_ops_infer64_8.dll`. The Rust bootstrap now side-loads them on CUDA machines; CPU/AMD/ROCm boxes skip the download and cache the result so their launches stay instant. (#827, #869)
- **`scripts/setup.py` no longer fails with `No module named pip` when installing the cuDNN 8 libs in the dev loop.** `uv venv` doesn't seed pip into the venv, so `python -m pip install` always broke; the script now uses `uv pip install --python` instead. (#869)
- **Generation timeouts now give device-honest advice.** A CPU-only machine is no longer told the GPU is "VRAM-starved" or to "set the engine to CPU" — CPU hosts get compute-bound guidance (shorter text, the CPU-tuned GGUF/Supertonic-3 engines, the OMNIVOICE_GENERATE_TIMEOUT_S knob) while GPU hosts keep the VRAM-contention explanation. (#896)
- **Model-download failures now name the mirror that failed.** When a Hugging Face mirror is configured and unreachable, every affected surface (generate, dub, Model Store installs) names the mirror and points at the exact setting instead of leaking a raw network error; auto-repair failures now say *why* the repair failed. (#874, #890)
- **No more infinite "preparing" after an unclean shutdown.** If Windows corrupts the WebView cache (e.g. after a BSOD), the splash detects the dead IPC channel, proceeds via a direct backend health check, and — if truly stuck — offers a one-click "Repair and restart". (#879, #892)
- **"Out of memory" is no longer the default excuse.** A failed model download mid-generation was mislabeled as OOM with useless "flush VRAM" advice; network failures are now classified honestly, only real OOM signatures get the OOM treatment, and first-use engine downloads retry once with a fresh connection. (#880, #893)
- **Hung transcriptions recover the same way everywhere.** Chunked dub transcription now shares the same guarded-timeout + GPU-pool reset as the rest of the app, and repeated timeouts recommend the crash-isolated ASR engine — now properly selectable in Settings. (#730, #895)
- **A raw `[Errno 22]` transcribe error now tells you what to fix.** When the OS rejects the temporary WAV write during dub transcription (a missing, read-only, or full temp directory, or antivirus interference), the stream used to dead-end as *"Transcription produced no segments. [Errno 22] Invalid argument"* with no next step; it now classifies the EINVAL and appends an actionable temp-dir/disk/AV hint — the same treatment the ffmpeg and compute-type failure classes already get. (#763)

- **Buttons can no longer hide under the logs footer on small windows.** The bottom status/logs bar was a fixed overlay that pages had to compensate for with padding — any view that missed it (voice-card grids in Gallery and Community, bottom action rows) clipped under the bar at small window sizes, a class previously patched one page at a time (#476, #504). The footer is now a real row of the app shell, so content physically ends at its top edge at every window size, collapsed or expanded — guarded by a new layout test plus a 900×600 Playwright check at the app's minimum window size.

- **Confucius4-TTS is now validated end-to-end — and actually loads.** The opt-in engine's first live run (Apple Silicon, CPU) caught three scaffold-era faults: the sidecar could never import `confuciustts` (upstream ships no packaging, so the documented `pip install -e` fails — the sidecar and bootstrap probe now put the clone on `sys.path`, like upstream's own example), the assumed 24 kHz sample rate was wrong (confirmed **22 050 Hz**, now regression-tested), and the docs demanded an Amphion/MaskGCT install that doesn't exist (all weights auto-download from HuggingFace). CPU is ~17× realtime, so CUDA stays the recommended path; `gpu_compat` now advertises `("cuda", "cpu")`. (#590)

- **Parakeet TDT transcription now works without an NVIDIA GPU.** The `nemo-parakeet` ASR engine (parakeet-tdt-0.6b-v3, 25 languages, word timestamps) was hard-gated behind CUDA — but a live measurement on an Apple Silicon M2 shows it transcribing at ~10× realtime *on CPU*, roughly 20× faster than the default whisper-large-v3 on the same machine at equal accuracy. The false GPU gate is removed, so Mac and CPU-only users can now pick the dramatically faster engine in Settings → Engines.

- **8 GB GPUs: voice-clone/dub transcription no longer kills the backend.** On cards where the TTS model already held most of the VRAM (e.g. RTX 4060 Ti 8 GB), loading whisper `large-v3` in float16 for a reference-clip or dub transcription died as a *native* CUDA out-of-memory abort — the whole backend process vanished with no error logged, and the app showed "Can't reach the local VoiceStudio backend." A new VRAM preflight re-checks free GPU memory right before the ASR load and steps down float16 → int8 → CPU instead of attempting a load that can't fit (opt-out: `OMNIVOICE_ASR_VRAM_PREFLIGHT=0`). (#723)

### CI

- **A migration can no longer silence the app's logs.** Alembic's startup config was disabling every existing logger process-wide (a latent bug the new pre-migration backup logging exposed); fixed, and the migration-safety tests are now immune to full-suite ordering. (#909, #917)
- **Deterministically green tests + real install proof.** Tests can no longer read the developer's real `.env` or app data (the order-dependent flake class, #878, #894), and a new cross-platform install-test workflow builds all four installers and proves a real first run — model download plus verified synthesis — on macOS, Windows, and Linux runners.

## [0.3.8] — 2026-07-01

A stability-focused release that makes first-run and Windows "just work," ships
**live, faster-than-real-time local dictation** and a **user pronunciation
dictionary**, and gives **Settings a full redesign**. It clears the wave of
**"Can't reach the local backend"** reports at the source — the 8 GB-card OOM
crash, the slow-load future-scheduling break, a Windows-only WhisperX load
failure, an ASR engine that couldn't load CTranslate2 on newer Linux/WSL, and
both transcription **and generation** stalls that *looked* like a dead backend
(a wedged GPU job now resets the worker pool and returns an actionable timeout)
are all fixed or now fail with a clear, actionable message. **macOS gets native file drag-and-drop back**
(including macOS 26 Tahoe). Downloads are faster out of the box (parallel
segmented transfer on by default) and the Hugging Face token that speeds them up
is front-and-center on setup. Plus multi-voice story casting, faster long-form
previews on Windows, and a friendlier, more honest batch of error messages
across dub, generate, and design (a corrupt-binary failure no longer poses as
"out of memory," a bad model id self-heals, and a stale dub job resets cleanly).

### Added

- **"Autofit" translation quality — the dub keeps the video's timing.** A new
  quality alongside Fast and Cinematic: the LLM rewrites each translated line so
  its target-language reading time fits *within* the segment's slot (a strict
  "never overrun" bound, per-language pronunciation-speed aware), so long
  translations no longer force the audio into a stressed >1.3× time-stretch.
  Cinematic still applies its reflect/adapt polish; Autofit adds the hard
  fit-to-slot pass on top. Needs an LLM (below); falls back to Fast with a clear
  notice if none is set. (#838)
- **A new LLM Providers settings page — bring your own high-quality LLM.**
  Settings → System → **LLM Providers** configures the LLM that powers Cinematic
  and Autofit translation. One page for **16 providers** — OpenAI, OpenRouter,
  Groq, Cerebras, Google AI (Gemini), Mistral, Cohere, NVIDIA, GitHub Models,
  Cloudflare, Hugging Face, SambaNova, SiliconFlow, plus **local Ollama / LM
  Studio** (fully offline, no key) and a **Custom** OpenAI-compatible endpoint.
  Paste a key, pick a model, **Test** the connection in one click, and "use for
  translation" to make it active. Keys are stored **encrypted** (the same
  at-rest protection as the HF token) and never leave the machine unless you
  choose a cloud provider; env vars still override for power users. The dub
  translate menu now routes you straight here when you pick a high-quality
  style without an LLM, instead of dead-ending on a toast. (#838)
- **A dedicated Network pane.** The HTTP/SOCKS proxy and FFmpeg-path controls
  (previously buried in General → Advanced) are promoted to their own category.
- **Factory reset in Storage.** A confirm-dialog-guarded action that clears the
  locally-saved UI preferences and reloads — without touching your voices,
  projects, or generated audio on disk.
- **Proactive, highlighted "Install" affordance for translation engines.** When
  you pick a Dub translation engine whose optional package isn't installed yet
  (e.g. Google / DeepL via `deep_translator`), the Engine selector now surfaces a
  bright accent **Install** button *before* you hit Translate — no more
  discovering the missing package only via a translate-time 400. On a from-source
  install it one-click installs into the backend's own interpreter; on a
  read-only **packaged build** it opens a popover with the exact `uv pip install …`
  command (copy-to-clipboard), a one-click **Switch to Argos (bundled, offline)**
  escape hatch, and a docs link. The install command is single-sourced in the
  backend registry, so the button and the 400 error can never disagree. New guide:
  `docs/dubbing/translation-engines.md`.

- **A user pronunciation dictionary that actually changes the audio.** Settings →
  General → Pronunciation lets you teach the engine how to say tricky words —
  each entry replaces a term with a respelling (`GIF` → `jiff`) right before
  synthesis, so it works on **every** engine, not just one. Scope an entry
  Global or to a single language (a German rule never fires on an English
  render), with longest-match-first, word-boundary-aware, case-insensitive
  substitution. For one-offs, write `[[word|respelling]]` inline in your text —
  it overrides the dictionary for that occurrence and never persists. A built-in
  Test field previews the substitution with no model call. Pure text transform,
  identical on macOS/Windows/Linux; plain text stays byte-identical, existing
  data upgrades cleanly via an additive migration. (Expressive-TTS Spec 01)

- **Live, faster-than-real-time dictation via a new sherpa-onnx ASR engine.**
  Pick one of seven small ONNX speech-to-text models (Parakeet TDT v3/v2,
  streaming Zipformer EN/ZH/bilingual, streaming Paraformer, multilingual
  Whisper Tiny) for dictation, and watch text appear *as you speak*. Streaming
  models emit partials frame-by-frame and commit a sentence on natural silence;
  offline models surface live partials too by re-decoding a growing buffer.
  Runs CPU-only and identically on macOS, Windows, and Linux — no GPU, no cloud,
  no extra setup beyond a ~75–180 MB one-time model download. Parakeet TDT v3 is
  the recommended default; existing Whisper/MLX/NeMo dictation engines are
  untouched and still the fallback.

- **New "Voice" settings panel for live dictation.** Settings → Capture now
  leads with a Voice card: an Enable Voice Dictation toggle (showing your real
  registered shortcut), a Toggle/Hold mode switch, and a Speech Model dropdown
  that lists all seven models with offline/streaming + recommended badges, size,
  one-line descriptions, the installed checkmark, and inline download/delete —
  reusing the model-store download progress. Picking an uninstalled model starts
  its download and switches to it once ready. **Toggle vs Hold** is wired for
  both the desktop global hotkey and the in-app Ctrl/Cmd+Shift+Space fallback, so
  the behaviour is identical on macOS, Windows, and Linux. While you speak, the
  dictation pill shows the transcript building **live**, and words type straight
  into the focused field *as you speak* — self-correcting with backspaces as the
  streaming recognizer refines, with clipboard-paste as an automatic fallback.

- **Tagged scripts auto-cast into a multi-voice podcast/audiobook.** Paste a
  `[Alice] … [Bob] …` script into Stories and hit Auto-cast: it now recognizes
  the `[Name]` tag format (alongside the existing `NAME:` screenplay and quoted
  prose), builds the cast, and assigns a voice per character automatically.
  Editing one line only re-synthesizes that line on export (the chapter cache
  is content-addressed), and inline markers like `[pause]` / `[voice:…]` are
  never mistaken for speakers. (#487)
- **A dedicated Contact page.** Discord, email, GitHub issues, and the project
  website (palash.dev) as clean one-tap rows, reachable from the footer — so
  reaching the maker is never more than a click away.
- **Live download speed, remaining size, and ETA on first-run setup.** The
  Models & Engines step now shows `38% · 5.2 MB/s · 1.2 GB left · ~3m` while a
  model downloads, instead of a bare "downloading…". (#657)
- **Turn off auto-play of the preview after a render.** New Settings →
  Appearance toggle, "Auto-play preview" (on by default) — switch it off so a
  finished clip doesn't start playing on its own, ideal when batch-generating
  segments. (#666)
- **App version in the status bar, one click from updates.** A `v<version>`
  badge sits by the network icon in the bottom bar; clicking it opens Settings →
  Updates, and it grows a pulsing dot the moment a new version is ready to
  install. (#671)

### Changed

- **Settings is now a sidebar-nav hub instead of an 11-tab strip.** The whole
  page was rebuilt from scratch as a grouped left-rail navigator (with a
  search/filter box) plus a scrollable content pane — the macOS System Settings /
  VS Code layout. Settings are organized into four groups and sixteen
  categories: **General** (Appearance · General), **Voice & Engines** (Engines ·
  Models · Dictation · Pronunciation · Translation), **System** (Performance &
  Device · Storage · Network · Sharing & Remote · Credentials), and **App**
  (Updates · Privacy & Reporting · Logs · About). Every existing control keeps
  its behavior and store/API bindings — this is a reorganization, not a rewrite.
  Typing in the search box filters the category list and jumps to the first
  match, and the rail collapses to a dropdown navigator below 760px so the full
  IA stays reachable on a narrow window. Categories whose changes need a backend
  restart (Models, Performance & Device, Sharing & Remote) carry a "restart
  required" badge.

- **The Settings pages got a full redesign — cleaner, denser, responsive.** A
  shared design system replaces the old patchwork: a left icon nav-rail,
  sentence-case section titles (no more debug-log uppercase), exactly one muted
  description per row, unified toggles/inputs, full-width content with proper
  padding, and horizontal font/theme pickers. Premium and compact instead of
  sparse and cluttered, and it adapts cleanly to window width. (#686, #690, #696)
- **Adding a Hugging Face token on first-run is now a one-line input right by
  Continue.** Was a bulky card buried at the bottom of the model list; it's now a
  compact "paste a token, Save" bar pinned next to the "Waiting for required
  models…" button, so you can add it (for faster, authenticated downloads)
  without scrolling. (#687, #688)
- **First-run setup is calmer and surfaces the best models for your machine.**
  Dimmed and tightened the setup descriptions (less wordy, more compact). The
  "Models & engines" step now shows the **platform-tuned** optional models up-front
  with a green "recommended" tag and their catalog note — e.g. MLX Whisper on
  Apple Silicon, CUDA-tuned variants on NVIDIA — instead of burying every optional
  model behind the fold (the universal long tail still folds).

- **Donations now go through Ko-fi or PayPal (GitHub Sponsors removed).** GitHub
  Sponsors isn't available, so the Support page no longer routes there: pick an
  amount (now $10 / $20 / $50) and then choose Ko-fi or PayPal — PayPal carries
  the amount straight into checkout. `.github/FUNDING.yml` and the README badges
  were updated to match.
- **Simplified the Commercial License page.** Trimmed the six-tile benefit grid
  and FAQ down to the three things that actually drive the decision (you own the
  output, no per-minute cost, direct support) plus one clear "request a quote"
  contact — less wall-of-text, faster to act on.
- **Model downloads are faster out of the box.** The built-in multi-connection
  (segmented) downloader — parallel byte-ranges with live speed/ETA — is now on
  by default, so the legacy-LFS path is no longer single-stream and slow. It
  falls back to the normal download on any error, so it can never compromise a
  correct install (`OMNIVOICE_SEGMENTED_DOWNLOAD=0` to disable). (#669)
- **The Hugging Face token is now front-and-center on first-run.** Was a
  collapsed "advanced" fold almost nobody opened; it's now a prominent card right
  above Continue, framed around what it actually buys you — authenticated, faster,
  more reliable downloads (higher rate limits, fewer stalls) — with a one-click
  "get a free token" link. (#657, #669)

### Fixed

- **Bug reports redact more secrets and every Windows username casing.** The
  opt-in bug-report scrubber now catches more credential shapes (JWT/Bearer,
  Google, Slack, AWS keys, and `?token=`/`?api_key=` URL secrets), redacts
  Windows home paths regardless of `Users`/`users` casing, and stops a superstring
  username (`/Users/john` vs `/Users/johnny`) from leaking a fragment. The
  prefilled-issue URL is now bounded by its *encoded* length so a large report
  can't silently truncate. Nothing new leaves the machine — this only makes the
  existing local-first, user-reviewed report stricter. (#856)

- **A hung TTS generate can no longer brick the backend ("Can't reach the local
  backend").** A GPU job that wedges on some Windows + CUDA setups occupies its
  worker forever — Python can't cancel the thread — so on the 1–2 worker pools we
  ship, one stuck job starved every other request and the next action surfaced as
  the misleading "Can't reach the local backend" even though the process was
  alive. ASR/dub/model-load already bounded and reset the pool on hang (#730); but
  **every generate path** — Studio synthesis, the streaming path, batch, the dub
  per-segment + preview render, archetype previews, and the OpenAI-compatible
  `/v1/audio/speech` API — was still an unguarded GPU dispatch, and the residual
  reports all failed on `generate:start (audio)`. Every one is now bounded by the
  same wall-clock guard (`OMNIVOICE_GENERATE_TIMEOUT_S`, default 300s) that
  abandons the wedged worker and rebuilds the pool, so capacity is restored
  automatically and you get an actionable timeout instead of a dead backend.
  Closes the whole class of GPU-job-hang reports (#851 — #850, #802, #755, #723,
  #721, and the 0.3.7 cohort, all tracked in #730).

- **An unsupported GPU now falls back to CPU instead of 500-ing every generate.**
  When the installed PyTorch build has no kernels for your GPU's compute
  capability — a too-old card (Pascal / GTX 10-series) or a too-new one
  (Blackwell RTX 50-series on pre-cu128 wheels) — CUDA failed at launch with the
  cryptic `CUDA error: no kernel image is available for execution`. The backend
  now detects that up front and runs on CPU (slower, but it works), and any raw
  occurrence is reported as "your GPU isn't supported — switch to CPU or install a
  matching PyTorch," not a Flush-the-memory dead end. Force the GPU anyway with
  `OMNIVOICE_FORCE_CUDA=1`. (#756)

- **The "TRANSLATION FAILED" banner now dismisses and clears itself.** The Dub
  translation-error banner used to be sticky — it survived a successful re-try and
  never went away. It now has a close (×), auto-clears on the next corrective
  action (re-translating, changing the engine, or installing the package), and
  self-clears after a short timeout — fixing the whole class of translate/pipeline
  banners that outlived the state that caused them.

- **Dubbing a video URL no longer fails with "ffmpeg is not installed."** yt-dlp
  downloads video and audio as separate streams and muxes them with ffmpeg, but
  it only looked on PATH — so on Windows (where VoiceStudio's ffmpeg is a bundled
  sidecar / `imageio-ffmpeg` binary off PATH) the merge aborted before the dub
  could start. yt-dlp is now pointed at the same ffmpeg VoiceStudio resolves. (#712)
- **A synth that succeeded no longer 500s because of a history-logging hiccup.**
  If the local database somehow missed schema init, recording the clip to
  generation history failed with *"no such table: generation_history"* and
  surfaced as a 500 — even though the audio had already been generated and saved.
  The write now self-heals the schema and retries, and a history-logging failure
  never fails the generation: you get your audio regardless. (#710)
- **Long-video dubs no longer spike RAM during assembly.** Dub generation used
  to hold every segment's audio in memory until the whole track was mixed, so a
  50-video batch or a single feature-length dub could exhaust RAM and crash. Each
  segment now streams to disk as it's rendered and the final track is assembled
  from those files via a 30s-chunk memmap writer, keeping memory flat regardless
  of video length. Per-segment download WAVs and the final track stay correctly
  watermarked (marked once at synthesis, no double-mark), and zero/negative-length
  segments no longer crash the run. (#639)
- **A corrupt or wrong-architecture native component no longer masquerades as
  "out of memory."** A synth failure caused by a bad `.dll`/`.pyd`/`.exe` on
  Windows (`[WinError 193] %1 is not a valid Win32 application` — e.g. torch,
  ffmpeg, or an engine binary) was labelled *"ran out of memory — try Flush,"*
  sending users down the wrong path. It now says the component is corrupt or
  built for the wrong architecture and to reinstall/repair it. (#705)
- **A "[Errno 32] Broken pipe" mid-generation no longer poses as "out of
  memory."** When the desktop app that launched the backend closes or relaunches,
  the backend's output pipe breaks and a synth can fail with `[Errno 32] Broken
  pipe`. That was labelled *"ran out of memory — try Flush,"* which never helps;
  it now tells you the backend lost its pipe and to restart the app. (#715)
- **Settings content no longer sprawls or spills out of view.** The content
  column capped at 1280px, so on wide windows rows stretched edge-to-edge with a
  big empty gap between each label and its control ("too spread out"), and a few
  panels (API keys, the shared button rows, appearance scale) used rigid pixel
  widths that pushed controls past the card's padding on narrow content. Now the
  content sits at a readable measure (a single `--settings-measure` token), the
  shared button/badge rows wrap instead of overflowing, rigid widths can shrink,
  and rows decide whether to sit side-by-side or stack based on their **actual**
  width (a container query) — not the viewport, which the 168px nav rail skews.
  Everything stays inside its padding, edge to edge, on every width. (#696)
- **File drag-and-drop works on macOS again.** The app's drop zones use HTML5
  file drops, but Tauri intercepts OS drag-and-drop by default (`dragDropEnabled`)
  and swallowed the files before the webview saw them — most visibly on macOS
  WKWebView, and fully broken on macOS 26 (Tahoe), where dropping a file did
  nothing. Disabled the interception so the webview handles native HTML5 drops
  on every platform. (#700)
- **A misconfigured `OMNIVOICE_MODEL` no longer bricks model load with a 500.**
  A stale or leaked TTS *engine id* (e.g. `omnivoice`) reaching the model loader
  used to fail every launch with *"omnivoice is not a local folder and is not a
  valid model identifier."* It now self-heals — only a real HF repo id
  (`org/repo`) or an explicit local path is honored; anything else falls back to
  the default with a logged warning. Every consumer of the setting routes through
  the same resolver, so a bad value also can't silently disable model warm-up,
  mislabel the Settings checkpoint, or get baked into an exported persona bundle.
  (#693)
- **ASR no longer crashes the dub/transcribe preflight when CTranslate2's native
  library can't load.** On hardened kernels / newer glibc (e.g. WSL2) the
  CTranslate2 `.so` is rejected with *"cannot enable executable stack"* — an
  OSError the WhisperX/faster-whisper checks didn't catch, so it took down the
  whole preflight. They now report the engine as unavailable and auto-detect
  falls back to PyTorch-Whisper instead of dead-ending. (#692)
- **A wedged transcription can no longer take the whole backend offline ("Can't
  reach the local backend").** On some Windows + CUDA setups a whisperx/CTranslate2
  transcribe hangs hard and never returns. Because ASR shares a small (1–2 worker)
  GPU pool with TTS, one stuck worker starved every other request — so the next
  thing you did (often a TTS *generate*) failed with "can't reach backend" even
  though the process was alive. Two fixes: every transcribe path — whole-file
  (dub whole-file, batch, live dictation) **and** the chunked dub stream — is now
  wall-clock **bounded** like the dub QC / dictation / OpenAI paths already were;
  and on timeout the poisoned GPU worker is **abandoned and the pool rebuilt**, so
  capacity is restored without restarting the app. You still get an actionable
  message (Flush VRAM / pick a smaller ASR model) for the durable fix. (#730)
- **The stale-dub-session recovery now also covers the first upload/ingest, not
  just retry/import.** A dubbing job that vanished server-side during the initial
  transcribe flow showed the scary *"Job not found … report a bug"* toast; it
  now resets gracefully and invites a fresh upload, like the other paths. (#695)
- **In-app preview of finished audiobooks/stories now plays on Windows.**
  The preview decoded the entire render into one in-memory PCM buffer via Web
  Audio `decodeAudioData`, which fails on long-form `.m4b`/AAC under WebView2
  (`EncodingError: Unable to decode audio data`), and the blob-URL fallback can't
  play in a Tauri `<audio>` element — so nothing played. The fallback now uploads
  to the preview endpoint (ffmpeg-extracts a streamable WAV) and plays the HTTP
  URL, the same path video previews use. Short TTS previews are unchanged. (#653)

- **First-run setup splash no longer shows a raw `bootstrap.lines` key in English.**
  The log-line counter string was present in 4 locales but missing from the `en`
  reference, so English (and 16 other locales falling back to it) rendered the
  literal key instead of "{{count}} lines". Added it to `en`. Also removed 160
  dead `gallery.cat_*` keys (renamed to `archetypes.use_*` long ago) orphaned
  across 20 non-English locales, clearing the i18n orphan-key advisory.

- **Backend no longer hangs on startup (unreachable, no error) on Apple-Silicon Macs.**
  The MCP session manager could hang on its anyio task group during lifespan
  startup (observed on M1, #632); because that start was awaited before the server
  began serving, "Application startup complete" never fired and the whole backend
  was unreachable. The MCP start is now timeout-bounded (`OMNIVOICE_MCP_START_TIMEOUT_S`,
  default 30s) — a hang becomes a logged warning and the backend serves normally
  without MCP, instead of wedging. (#632)

- **Dubbing a URL no longer fails with `[Errno 22] Invalid argument` on Windows.**
  yt-dlp stamps the downloaded file's modified-time with the video's upload
  date; an out-of-range/invalid timestamp makes the `os.utime` call raise
  `[Errno 22]` and aborts the whole URL ingest. VoiceStudio downloads to a throwaway
  file and never uses its mtime, so it now skips the stamp entirely
  (`updatetime=False`). (#642)

- **Dubbing a YouTube link that 403s now retries with a different player
  client.** Some videos serve their formats signature-protected to the default
  player client, so the media download fails with `HTTP Error 403: Forbidden`
  even though extraction worked — and a plain retry keeps 403ing. The URL
  download now escalates the YouTube player client (tv → android → web_safari)
  on a 403, which commonly bypasses it, before surfacing the actionable error.
  (#625)
- **A synth glitch that produced unreadable audio is now caught instead of a
  misleading "out of memory".** A numerical glitch in the model (seen on Apple
  Silicon/MPS) could leave NaN/∞ samples, which wrote a WAV that then failed
  decoding with an opaque `ffmpeg returned error code: 183 / Invalid data` — and
  the generic error handler labelled it "ran out of memory". Non-finite samples
  are now sanitized to silence before any encode (so the WAV is always
  decodable), and a genuine decode failure is reported as "unreadable audio —
  Flush and regenerate", not OOM. (#629)
- **A silent startup hang now leaves a diagnostic instead of nothing.** On some
  setups the backend could load all model weights and then hang forever before
  "Application startup complete" — no error, no crash, an unusable app (reported
  as a Mac M1 hang after `Loading weights: 527/527`, #632). A startup watchdog
  now dumps every thread's stack to the error log if startup stalls past a
  window (default 5 min, `OMNIVOICE_STARTUP_WATCHDOG_S` to tune, `0` to disable),
  so the deadlock is captured rather than invisible. It's disarmed the instant
  startup finishes, so a normal (even slow-first-download) boot never trips it.
  (#632)
- **First-run demo voice is back.** The bundled demo clip
  (`backend/assets/samples/demo_voice.wav`) was a build artifact that never got
  committed, so it shipped absent — onboarding logged "Demo audio not found" and
  seeded nothing, leaving a brand-new install with an empty Launchpad and no
  `/demo_audio` route. The clip is now committed (it's already un-ignored and
  bundled via the Tauri `backend` resource), so first-run seeds the demo voice
  on every platform; onboarding still degrades gracefully (with a regenerate
  hint) if it's ever absent. (#621)
- **Multi-speaker dubbing: two speakers' turns merged onto one line are now
  split apart.** Segmentation groups words into sentences *before* diarization
  runs, so a back-and-forth exchange could land in a single segment; the speaker
  pass then only *relabelled* that segment with its majority speaker, losing the
  turn boundary (the second half of #486; the per-speaker voice auto-assign was
  fixed earlier in #490). A new post-diarization pass re-splits any segment whose
  words span more than one speaker at the word-level boundary, assigning each
  piece its own speaker. Single-speaker segments pass through **byte-for-byte
  unchanged**, so single-speaker dubs and their timing never move, and a lone
  mis-attributed word (diarization noise) is smoothed rather than causing a
  spurious split. (#486)
- **Designed voices saved with a bad style no longer render wrong or crash
  generation.** A designed voice could persist an `instruct` the engine
  validator rejects — either the literal `"[object Object]"` from an old build,
  or freeform prose typed into the style field — which made every generation or
  dub that used the voice fail with `Unsupported instruct items found in …`
  (surfacing to users as a 400/500 and, when it tore down mid-render, "Can't
  reach the local backend"). The previous fix only *blanked* `"[object Object]"`,
  which silently dropped the design — so an Indonesian **female** voice came out
  **male**. Now the stored instruct is sanitized down to valid tags at every
  seam (save, edit, and when a profile drives Generate or Dub), and when the
  stored value is unusable the tags are **rebuilt from the design's saved
  category picks (`vd_states`)** so the intended gender/age/pitch/accent survive.
  A migration (0007) heals existing poisoned profiles in place — no reinstall,
  no manual fix. (#550 #571 #594 #596)
- **"Transcribe stream dropped … Likely ASR backend failed to load" now shows
  the *real* reason.** When transcription failed to load its ASR model (the
  reported case was WhisperX on Windows — typically a faster-whisper /
  CTranslate2-cuDNN mismatch, a missing model download, or the torch-2.6
  weights-only VAD regression), the UI dead-ended on a generic "stream dropped"
  message with no actionable cause. Two root causes: (1) WhisperX loads lazily
  *inside* transcription, so the load failure was buried in per-chunk errors and
  retried on every chunk; the transcribe pre-flight now eagerly loads the ASR
  model (new `ASRBackend.ensure_loaded()`), surfacing the genuine cause once, up
  front, as a structured error. (2) Pre-flight and audio-load errors closed the
  SSE stream with a bare `error` and no terminal `done`, so the browser's native
  EventSource connection-drop could race and win against the structured error —
  discarding the real cause and falling back to the generic message; every
  terminal error now emits `done`, and the frontend latches the structured cause
  so a connection drop can't overwrite it. Net: WhisperX load failures are
  diagnosable instead of a silent dead-end. Fail-before/pass-after regression
  test included. (#578)
- **Dubbing: the PLAY button on the dubbed-video preview did nothing.** Same
  autoplay-policy trap that #510 fixed for the standalone audio player, but the
  dub editor's timeline player was missed. WaveSurfer builds its `AudioContext`
  at mount — before any user gesture — so on Windows WebView2 (and Linux
  Firefox/Chrome, Android Chrome) it stays `"suspended"`; `playPause()` then
  resolves with no sound and the preview just sits there. Every playback entry
  point in the dub timeline (the toolbar Play button and the per-segment "play
  this slot") now resumes the context via the shared `unlockAudio()` on the
  click before starting playback, and swallowed play() rejections are logged
  instead of hidden. A source-contract regression test pins the invariant so a
  future refactor can't quietly reintroduce a silent play path. macOS is
  unaffected (its context was never blocked). (#595)
- **Voice design: the script text field couldn't be expanded.** The Script
  textarea was a `flex: 1` item inside a flex column, so flex-grow recomputed
  its height on every reflow and snapped the user's drag back — `resize:
  vertical` is silently ignored on a flex-grown item in Chromium/WebView2. The
  field now owns its own height (starts taller, and the corner grip grows it
  reliably on every platform). (#595)
- **An interrupted model download now self-repairs instead of dead-ending.**
  When the VoiceStudio TTS cache was missing weight shards (the usual aftermath of
  an interrupted first download), the next synthesize failed with a 500 and a
  "delete the model and install it again" instruction — a manual dead-end. The
  backend now detects the truncated-cache error on load, re-fetches just the
  missing files via `snapshot_download` (already-present blobs are skipped, so a
  near-complete cache repairs in seconds and a healthy cache is never touched),
  and retries the load automatically. Offline mode (`HF_HUB_OFFLINE`) is
  respected — repair never makes a network call the user opted out of — and if
  the re-fetch still can't fix it, the actionable delete-and-reinstall message
  is preserved as the fallback. (#581) The repair now also **retries** the
  re-fetch (3 attempts, resuming each time) so a single transient blip — the very
  thing that interrupts a download in the first place — doesn't bounce you back
  to a manual reinstall; tune with `OMNIVOICE_MODEL_REPAIR_RETRIES`. And if a
  resume-repair still won't load — the signature of a *corrupt* file that kept
  its size, which a resume trusts and never re-fetches — it now **force
  re-downloads** the model files once before giving up, so even a bit-rotted
  cache self-heals without a manual reinstall. (#739)
- **Dubbing a YouTube URL no longer dies on a transient "Broken pipe."**
  Pasting a video link could fail outright with `download: Unable to download
  video: [Errno 32] Broken pipe` — a broken pipe raised while the write side of
  a pipe closes mid-stream (a killed ffmpeg merge child, a CDN reset during
  muxing). yt-dlp's own per-fragment retries don't cover that case, so a single
  transient blip aborted the whole ingest. The URL download now retries up to
  twice on broken-pipe / network-drop failures, wiping the partial download
  between attempts, and only surfaces the (already-actionable) "connection
  dropped — just retry" hint after the retries are exhausted. Unsupported links
  still fail fast with their own hint — no wasted retries. (#579, #598)
- **`No module named 'omnivoice'` on installs whose venv lost its editable
  record.** An interrupted or offline `uv sync` (common during an in-place
  upgrade) could install all dependencies yet never lay the editable install of
  the project's own `omnivoice` package — or an antivirus quarantine could
  remove it. The venv still started uvicorn, so the bootstrap's health gate
  passed it through, and the app only failed at the first generate/dub with
  `No module named 'omnivoice'`. The bootstrap now also verifies `omnivoice` is
  importable (via a cheap `find_spec`, no torch load) and forces a repair
  `uv sync` that re-lays the editable install when it isn't; the backend also
  resolves `omnivoice` from its bundled source tree at runtime as a safety net.
  No reinstall needed — relaunch and it self-repairs. (#564)
- **"cannot schedule new futures after shutdown" no longer breaks generate/dub
  after a slow first load.** When a model load timed out, the backend reset its
  GPU worker pool to recover — but several request handlers had captured the old
  pool object at import time and kept submitting to it, so every subsequent
  generate, dub, transcribe, or translate failed with `cannot schedule new
  futures after shutdown` (a 500, or "Can't reach the local backend" when it
  took the worker down). The GPU pool is now a single self-healing handle whose
  worker pool is rebuilt on demand, so a reset can never strand an in-flight or
  later request. No settings change; the recovery is automatic. (#589 #599)
- **Transcription / dubbing works on Windows again.** WhisperX failed to load on
  Windows because speechbrain's guard that suppresses stray optional-integration
  imports used a POSIX-only path check, so a `k2_fsa` import error aborted the
  whole transcription. Fixed cross-platform — covers the entire class of optional
  integrations, not just k2. (#630 #611 #647)
- **A slow transcription no longer looks like a dead backend.** Whole-file
  transcribe paths (dub QC, dictation, OpenAI-compat) ran unbounded, so a
  VRAM-starved `large-v3` could spin for minutes and hold a GPU worker — surfacing
  as "Can't reach the local backend". They're now time-bounded and return a clear,
  actionable 504 (free VRAM / pick a smaller ASR model / use CPU) instead of
  hanging. New troubleshooting section documents it. (#656)
- **Windows preview playback fixed.** The audiobook/clone preview's streaming
  fallback fetched `localhost`, which on Windows resolves to IPv6 and missed the
  IPv4-only backend — so previews failed with "decode error" / "no supported
  sources". The preview API now targets `127.0.0.1` (matching the main client),
  and the expected decode→stream fallback is logged calmly instead of as a scary
  error. (#653 #659)
- **A stale dub session resets cleanly instead of erroring.** Reopening the Dub
  tab after the backend restarted tried to resume a job that no longer existed and
  surfaced "Job not found" as a bug-report error. It now quietly clears the dead
  session and invites a fresh upload. (#660)
- **A bad voice-style instruct is a clear 400, not a scary 500.** Typing free-form
  prose (or a non-English description) into the style/instruct field returned a
  500 telling you to Flush for memory you never ran out of; it now returns a clean
  400 that lists the valid style tags. The Voice Clone UI also drops unrecognized
  style text locally and generates anyway. (#664 #612)
- **The ⊕ Insert token popover stays on screen.** On Voice Clone it could grow
  tall enough to clip off the top of the window; it's now a compact, scrollable
  box anchored above the button. (#672)
- **First-run no longer hangs on Apple Silicon.** The MCP session-manager startup
  is now timeout-bounded so a slow/stuck mount can't wedge the whole backend boot
  on M1. (#632)

### CI

- **Feature-coverage test system.** A backend route-inventory test diffs all 213
  HTTP/WebSocket endpoints against a committed snapshot (plus a critical-endpoint
  guard and a route-count floor), and a frontend feature-coverage test asserts
  every app mode is wired to a page and every feature has its i18n namespace — so
  an endpoint or page silently disappearing now fails CI on every PR.
- **`bun desktop` no longer kills its own dev backend.** The dev launcher runs the
  API and the Tauri app side-by-side, but the app's backend manager would "take
  ownership" of port 3900 and kill the API the moment it booted (before it was
  healthy), tearing the whole session down. The dev app now sets
  `TAURI_SKIP_BACKEND` so it attaches to the running API instead of fighting it —
  production launch is unaffected. (#745)

## [0.3.7] — 2026-06-20

A stabilization release that clears the wave of issues reported on the 0.3.6
line — across voice design, dubbing, transcription, install, and the Linux/web
UI — and lands two more opt-in cloning engines. The throughline is **non-English
correctness and cross-platform playback**: cloned and designed voices now hold
their language end-to-end, and audio plays inline in Linux/Android browsers,
not just macOS. It also carries the v0.3.6 startup-crash fixes, so anyone still
hitting "Can't reach the local backend" on v0.3.5/v0.3.6 only needs to update.

### Added

- **Two opt-in heavyweight TTS engines: MOSS-TTS-v1.5 (8B) and dots.tts (2B).**
  Both are zero-shot voice-cloning engines, each running in its own isolated
  subprocess venv (they pin a `transformers` version that conflicts with the
  parent's `>=5.3` — MOSS `==5.0`, dots.tts `==4.57`) via the same dedicated-venv
  pattern as IndexTTS-2, so they can't disturb the default install or its
  lockfile. Point `OMNIVOICE_MOSS_TTS_V15_DIR` / `OMNIVOICE_DOTS_TTS_DIR` at a
  local clone to enable. CUDA/CPU only — neither claims Apple-Silicon MPS, and
  dots.tts is gated off on Windows (upstream is Linux/macOS only). See
  [docs/engines/moss-tts-v15.md](docs/engines/moss-tts-v15.md) and
  [docs/engines/dots-tts.md](docs/engines/dots-tts.md). (#498)

### Fixed

- **Non-English voices drifted to English / the wrong language.** Three
  independent root causes, all in the language path: (1) a voice profile's
  stored language was never read back into generation, so a German archetype
  that *previewed* in German *generated* in English (the preview passed the
  language; the user's Generate call didn't); (2) the audiobook/longform synth
  hardcoded `language=None`, letting the engine re-autodetect per chunk so a
  non-English clone could flip language mid-render on short/ambiguous lines; and
  (3) the duration estimator weighted Unicode combining marks at zero, so
  decomposed (NFD) diacritic text — common for Vietnamese — under-allocated
  frames and came out rushed. The profile/request language is now threaded
  through both the single-shot and longform paths (request wins, profile fills
  the gap), and text is NFC-normalized before duration estimation. Each fix has
  a fail-before/pass-after regression test. (#533, #505, #502)
- **Audio playback on Linux Firefox/Chrome and Android Chrome.** Two separate
  root causes both masquerade as "the play button doesn't work" on non-macOS
  browsers — and both are invisible when developing on macOS, which is why they
  shipped. (1) The backend served `.wav` / `.flac` with Python's default
  `audio/x-wav` / `audio/x-flac` (vendor-experimental, never IANA-registered);
  macOS CoreAudio MIME-sniffs leniently and plays anyway, but Linux FFmpeg and
  Android ExoPlayer strictly honor the declared type and prompt to download.
  Fixed by registering the canonical `audio/wav` / `audio/flac` types before
  any `StaticFiles` mount. (2) WaveSurfer's `AudioContext` is constructed at
  component-mount time — i.e. before any user gesture — so on Linux FF/Chrome
  and Android Chrome it stays `suspended`, `decodeAudioData` hangs, the
  `ready` event never fires, and the play button never enables. macOS
  Safari/Chrome auto-resume on first interaction. Fixed by patching
  `window.AudioContext` to track every instance and resuming them on the first
  `pointerdown` / `keydown` / `touchstart`, plus resuming inline on the play
  click itself. The MIME fix has a backend regression test; the unlock path
  has a Vitest unit test covering idempotency, post-unlock contexts, and
  error isolation. (#510)
- **Voice Studio "Save design as profile" poisoned the profile with
  "[object Object]" and then 400'd every generation** ("Unsupported instruct
  items found in [object Object]"). The save passed the instruct *builder
  object* to the form instead of its string. Fixed at the source + defended with
  a coercion helper; the engine now tolerates the sentinel, and a migration
  heals already-saved profiles. (#550, #545, #542, #537, #530, #525)
- **Profile / persona / consent endpoints 500'd with `no such column:
  consent_audio_path`** (and the same class for `kind`/`vd_states`/…) after an
  in-place upgrade. The alembic migration existed but couldn't always apply
  (stamped at a removed revision, or alembic not importable) and the failure was
  swallowed. The runtime schema now self-heals — it ADDs any missing additive
  column from the canonical schema on startup. (#552, #547)
- **Stories: the global reading-speed slider was ignored by preview and stem
  export.** The #415 global speed only flowed through the full longform export;
  per-segment preview and stem export still resolved a hardcoded `track.speed ||
  1.0`, so audio played at 1.0× even with the global set to e.g. 0.70×. A shared
  `effectiveSpeed(track, global)` helper (per-line override → global → engine
  default) now drives all three generation paths. (#508)
- **Generate / Settings / Clone buttons were missing / unpressable on Linux.**
  The UI-scale fix round-trips correctly on Chromium, but older WebKitGTK treats
  `zoom` as a layout no-op, leaving a ~23% black band that pushed the bottom CTAs
  off-screen. The shell now probes the engine and fills the window when `zoom`
  doesn't lay out. (#523, #524)
- **Settings tabs with little content rendered as a stunted box in a black
  void** (reported on Appearance). The page is now a flex column with a
  min-height floor — short tabs fill the panel, tall tabs grow and scroll
  exactly as before. The Appearance panel's previously hardcoded English
  strings ("UI scale", "Color theme", "Font") were also routed through i18n,
  per the localization rule. (#507)
- **The engine "Install" button 500'd with "No virtual environment found."**
  `uv pip install` now targets the running interpreter (`--python
  sys.executable`) instead of relying on a venv it couldn't auto-discover.
  (#529, #527)
- **Transcription failed with "no segments" on GPUs without efficient float16.**
  Both CTranslate2 ASR backends now fall back float16 → int8 instead of crashing
  at model load; a transcribe stream can no longer close without a terminal
  error event; and an incomplete `transformers` install reports an actionable
  message instead of "Could not import module 'AutoFeatureExtractor'".
  (#551, #549, #516)
- **Audiobook import 500'd** with `'AudiobookPlan' object has no attribute
  'chapter_count'` for every format (.txt/.md/.epub/.pdf). (#543)
- **Windows: generated audio auto-played in a separate, un-closeable black
  window.** Renders now play in-app through the shared playback manager. (#532)
- **Cryptic video-download errors** now carry actionable hints: an unsupported
  link shape ("paste a direct video page, not a share/feed link") vs a transient
  network drop ("just retry — the partial download was cleaned up"). (#554, #536)
- **A relocated, copied, or restored backend venv ("No module named
  'encodings'") now self-heals** (rebuilds once) instead of failing on every
  launch.
- **The donate goal bar showed fabricated progress** ($137.50 / $200, 23
  sponsors). It now reflects the real figures ($10 / $200, 1 sponsor) in both the
  runtime JSON and the TypeScript fallback. (#513)
- The **"Can't reach the local backend" startup-crash wave** (pkg_resources
  #248, `scalar_fastapi` #307, exit-106 broken venv) was fixed in v0.3.6 — this
  release carries those fixes, so updating from v0.3.5/older resolves them.

### Changed

- **Version is now single-sourced from `frontend/package.json`.** Five
  hand-maintained literals drifting is exactly what shipped a 0.3.6 build that
  called itself 0.3.5. `package.json` is canonical (vite already injects it as
  `__APP_VERSION__`), `tauri.conf.json` reads its bundle version from it
  (`"version": "../package.json"`), and the remaining toolchain-required mirrors
  (Cargo.toml, pyproject.toml, the frozen-backend fallback) are CI-guarded to
  stay in lockstep. (#503)
- **Updater: the Preview channel actually tracks `main` again.** It was stuck at
  `0.3.5-41` because its only build trigger was a manual dispatch; a nightly
  rebuild now enforces "preview = main" (no-opping on days `main` didn't move).
  Two latent hazards are closed: the `preview` release is re-asserted as a
  prerelease every run (a non-prerelease preview could hijack the Stable
  channel's "Latest"), and its manifest can no longer silently drop the
  Intel-Mac (darwin-x86_64) target. (#500)

### Internal

- **The frozen desktop backend reported `0.3.5` regardless of its real version.**
  In a synced env, `core.version.APP_VERSION` resolves from package metadata
  (correct, so CI stayed green), but the PyInstaller-frozen build has no
  `.dist-info`, hit `PackageNotFoundError`, and fell back to a hardcoded literal.
  The spec now bundles `omnivoice` metadata so the primary path works frozen too,
  and the resolution chain is metadata → pyproject → named fallback. This also
  fixes **About → Version rendering blank** in the web/Pinokio build (no Tauri,
  backend idle), which now falls back to the build-time version. (#501)

## [0.3.6] — 2026-06-16

A large release (168 commits since v0.3.5). The headline is the **Longform
suite** — produce full audiobooks and multi-voice stories from text, EPUB, or
PDF — alongside a real **engine-routing** layer that tells you up front when an
engine will fall back to CPU instead of finding out mid-synth. Dubbing,
first-run, and install reliability all get a pass too.

### Added

- **Longform: Stories + Audiobook editors.** Two new tabs turn long text into
  finished audio. **Audiobook** takes a script (or imports plain text / EPUB /
  PDF), auto-splits it into chapters, and renders a chaptered `.m4b` with
  metadata, cover art, and per-chapter preview/resume. **Stories** is a
  multi-voice editor — assign a different voice per line, preview, and export
  the whole thing through the same server-side renderer. Both share one render
  core (loudness, metadata, cover art) and one live SSE progress stream, and
  you can convert a project between Story and Audiobook in place.
  (#402, #403, #404, #408, #409, #411, #412, #413, #426, #435, #436, #447)
- **Longform: PDF & EPUB ingest.** "Import" on the Audiobook tab accepts EPUB
  and PDF (not just plain text) and auto-chapters the result, so an existing
  ebook becomes an audiobook without manual copy-paste. (#412, #459)
- **Longform: two-pass loudnorm mastering.** Audiobook/Story exports now run a
  measure-then-normalize loudnorm pass for accurate ACX/podcast loudness
  targets. A slow or broken measure pass degrades gracefully to single-pass
  rather than aborting the render. (#449, #455)
- **Longform: crash-resume.** An interrupted render is resumable without
  re-submitting the original input — the compiled plan is persisted to the job
  dir and finished chapters are reused, so a crash mid-book doesn't cost you the
  whole render. (#470)
- **Longform: pronunciation control + SSML-lite prosody.** A per-render
  pronunciation lexicon (word respelling) plus an in-app pronunciation editor
  and markup reference, and inline prosody markers — `[slow]` / `[fast]` /
  `[emphasis]` / `[spell]` — for fine-grained delivery. (#419, #421, #422)
- **Stories: global reading-speed control.** A toolbar slider (0.5–2.0×) sets
  one speed for every line that doesn't have its own per-line override; the
  per-line slider still wins. Persisted as a UI preference. (#415, #416)
- **Unified LongformProject store.** Audiobook metadata, scripts, and prefs
  persist in a single project store (with a `v4→v5` migration), and finished
  books/stories now show up alongside other work in **Projects**. (#417, #443,
  #444)
- **Portable personas (`.ovsvoice`).** Export any voice as a self-contained,
  fully-local persona bundle — identity, optional reference clip, consent
  attestation, SPDX license, and a watermarked preview — and import it back into
  another VoiceStudio install. A privacy toggle ships a **preview-only** bundle so
  no raw recording of your voice has to travel. Verified-own-voice status can't
  be forged by hand-editing a bundle (real recording + consent text + attestation
  required). Legacy `.omnivoice` files still import. See
  [docs/persona-format.md](docs/persona-format.md). (#29)
- **Engine routing — no more silent CPU fallback.** A host device probe and
  routing resolver now decide where each engine actually runs, and the verdict
  is surfaced before you hit Synthesize: the **Settings → Engines** picker shows
  a per-engine compatibility matrix, and **preflight** / **diagnose** report the
  active engine's GPU verdict (accelerated / caveat / CPU-fallback /
  unavailable). At synth time every TTS entry point (`/generate`,
  `/v1/audio/speech`) enforces the same routing — an engine that can't use this
  host's GPU returns an explicit error or an `X-VoiceStudio-Routing` header instead
  of silently dropping to CPU or dying mid-synth. (#21)
- **Diagnostics suite.** New self-check tooling for when something's wrong: a
  `/system/diagnose` report (and matching backend `--diagnose`), a persistent
  **error journal** surfaced in Settings, and a scrubbed **diagnostic bundle**
  (home dirs stripped to `~/`, no tokens/keys) you can attach to a bug report.
  Paired with structured GitHub **Issue Forms** (bug / install / feature) for
  cleaner reports. (#433, #456)
- **Dubbing: multi-speaker per-speaker voice assignment.** When diarization
  detects multiple speakers, each segment is now bound to its speaker's cloned
  voice automatically instead of landing on "Default" and needing manual fixes;
  per-segment reference clips are still preferred for quality where present. Also
  adds an optional speaker-count hint for diarization. (#275, #486, #490)
- **Dubbing: Smart Fit timing + second-pass QC.** A Smart Fit timing strategy
  (planner, fingerprints, per-segment video retime + drift absorption + fitted
  subtitles) plus a second-pass ASR QC that flags lines whose dub drifts from the
  target timing — wired into the dub editor UI. Includes a timeline segment
  editor (drag, snap-to-onset, keyboard a11y), speech-onset alignment, regional
  dialect targeting, and per-segment clone references. (#280, #347, #350, #369,
  #370, #458)
- **Dubbing: dedicated Dub home.** A projects/history landing for dubbing with
  project rename. (#435)
- **Voice Console workspace.** Clone and Design are consolidated into one Voice
  workspace with right-side panels, a shared waveform player, an identity recipe
  line / Active-voice card, and a free-text "describe your voice" field that maps
  natural language to design parameters. (#317, #374, #376, #378, #395, #396,
  #397)
- **Unified first-run setup.** Nothing installs until you confirm a plan: pick an
  install mode (installed / portable), a storage location, and (on restricted
  networks) custom PyPI/HF/python-build-standalone mirrors — with a
  minimum-free-space gate before anything downloads. Followed by a guided
  studio-console wizard with platform-aware hints, resume reassurance, and
  download ETAs. (#286, #295, #297, #298)
- **Dictation: local-LLM refinement.** Opt-in local-LLM cleanup of final
  transcripts (collapsing Whisper hallucination loops), available on both live
  dictation and the REST `/transcribe` path; plus opt-in NLMS acoustic echo
  cancellation for dictating over playback. Configure a remote LLM endpoint
  (Ollama / vLLM / LM Studio) in Settings. (#356, #357, #363, #399, #400, #457)
- **Unlimited-length TTS + streaming.** Sentence-boundary chunking with
  crossfade removes the per-generation length cap, and a new sentence-by-sentence
  `/ws/tts` streams audio as it's produced. An inline `[pause Nms]` marker
  inserts measured silence in generated speech. (#276, #357, #358)
- **MCP server v1.** VoiceStudio mounts an MCP server on `/mcp` (with a stdio shim
  and per-agent voice binding) so it can act as a local TTS/STT provider for
  agentic pipelines. (#368)
- **Remote-backend access.** Point the desktop UI at a remote backend URL with a
  bearer key (Tailscale-documented), and an opt-in Hugging Face token field in
  the setup flow. (#303, #364)
- **"Fund Claude Max" support experience.** The donate page gets a real goal bar
  with a "Join N supporters" social-proof line and suggested amounts, plus Pip
  the mascot and a non-blocking "postcard" toast that appears only *after* a
  success (a finished dub, a saved clone, a longform export) — never on errors,
  setup, or first run — with escalating cooldowns and a one-click "don't ask
  again". (#494)

### Fixed

- **Transcription/dubbing failed when ffmpeg wasn't on `PATH`** (notably on
  Windows). WhisperX now decodes audio through VoiceStudio's own validated ffmpeg
  binary instead of a bare `PATH` lookup, so ASR works without a system ffmpeg
  install. (#479)
- **Translation defaulted the source language to English.** Dubbing/translation
  now guesses the source language from the text instead of assuming `en`,
  fixing wrong-direction translations. (#478)
- **Cinematic / LLM dubbing features failed out of the box** because `openai`
  wasn't bundled. The client is now a runtime dependency, so those paths work on
  a fresh install. (#484)
- **`pkg_resources missing` install dead-end (#248).** The auto-repair ran
  `uv pip install setuptools`, which `uv` treated as a no-op when setuptools
  *metadata* was present but its files had been removed (commonly by Windows
  Defender quarantine or a partial extract). Both repair sites now use
  `--reinstall` to force re-extraction, and the error/hint text suggests the
  working command plus an antivirus-exclusion note. (#248)
- **A stuck backend trapped users on a buttonless splash (#474).** The bootstrap
  splash now has a per-stage stall watchdog: if a non-terminal stage sits past
  its budget (20 min for dep install, 120 s otherwise), it flips to the failed
  state with actionable hints, the live log, and Retry / Clean-&-Retry — instead
  of polling forever with no way out. (#474)
- **Changing the model-download location in Settings had no effect (#480).** The
  desktop launcher injected a stale models dir that overrode the per-user value,
  so new downloads kept going to the old folder and "Effective location" stayed
  wrong. The per-user env file now wins, so the in-app Settings path is
  authoritative. (#480)
- **Backend crashed on app upgrade with a stale venv (#307).** Dependencies are
  now synced on upgrade, and a structurally broken venv self-heals instead of
  exiting `106`. `scalar_fastapi` is now optional so its absence can't break
  startup. (#307, #314)
- **`/generate` ignored the selected TTS engine (#312)** and GGUF speech-control
  parameters weren't forwarded — both now honored. (#306, #312)
- **TTS generation failed on some GPUs.** `torch.compile` failures now fall back
  to eager execution so generation never hard-fails on unsupported GPUs, and
  cudagraph-compiled inference is pinned to one dedicated thread to avoid
  crashes. (#278, #315)
- **Re-dub ignored transcript edits (#281).** Fingerprints are canonicalized, the
  preview cache is busted, and the mux is atomic, so editing the transcript and
  re-dubbing actually reflects your changes. Translated subtitles now burn in
  correctly and subtitle save no longer throws a JSON error. (#281, #309)
- **macOS: app wouldn't open without using Terminal.** Builds are now ad-hoc
  signed (with signing/notarization verification), so the app launches normally.
  (#290)
- **macOS dictation auto-paste stole focus**; it now writes the clipboard
  natively without grabbing focus, and microphone-permission handling adds OS
  usage descriptions, a WebView grant handler, and an actionable denied-state UI.
  (#287, #323)
- **Clone-reference transcription was broken** (it used a removed transformers
  pipeline); it now routes through the ASR registry. A crash-isolated
  faster-whisper subprocess backend keeps an ASR crash from taking down the app.
  (#308, #393)
- **Realtime status probe hit a gated route.** It now probes the auth-exempt
  `/health` instead of the gated `/model/status`, and the UI polls the backend
  over HTTP before opening the WebSocket to avoid startup `ECONNREFUSED`. (#439,
  #450)
- **Non-executable or unreachable engine binaries showed cryptic errors** — these
  now produce actionable messages. (#437, #438, #454, #466)
- **Design-profile save was coupled to a TTS render (#476)**, so saving a profile
  needlessly triggered synthesis; the two are now decoupled. (#476)
- **UI scale / black bands.** The app shell now scales via `transform: scale` and
  always fills the viewport, fixing the WebKitGTK black-band issue on Linux and
  cramped/black layouts at narrow widths — a permanent fix across platforms.
  (#445, #452)
- **Clone popover/CTA clipping and a non-resizable textarea** are fixed, the
  WaveformPlayer no longer pauses itself on play or ignores clicks, and several
  layout/history-display issues (phantom sidebar gap, title clamping, flicker)
  are cleaned up. (#379, #384, #398, #481)
- **Windows: `desktop-prod` now runs from cmd/PowerShell** via a cross-platform
  launcher, `tqdm` is disabled on non-TTY to avoid an `OSError`, and ffmpeg
  validation guards against `WinError 193`. (#282, #305, #377)
- **MLX import hardened** against PyInstaller dylib failures, with a proper
  platform gate so it's only loaded where it works. (#390)

### Changed

- **Restricted-network support.** A Hugging Face mirror (`HF_ENDPOINT`) setting,
  custom PyPI / HF / python-build-standalone mirrors in first-run setup, and
  region presets help installs complete behind restrictive networks. (#286, #391)
- **Engine memory management.** Subprocess-engine sidecars now unload on demand
  and idle-reap to free VRAM. (#401, #406)
- **Faster, more accurate model downloads** via a Xet fast path with accurate
  progress reporting, plus a model-management cleanup pass. (#424, #428)
- **Voice profiles unified** under one model with a `kind` discriminator and
  stored design params, and consent-locked profiles (`verified_own_voice` +
  spoken-consent flow). (#354, #376)
- **Updater** preview channel now offers the newest build across channels, and
  preview versions carry an MSI-legal numeric pre-release stamp. (#293, #326)
- **Performance.** Voice-clone prompt embeddings are cached, and dub retime
  batches seek to their window instead of decoding from frame 0. (#387, #427)

### License

- **Relicensed from FSL-1.1-ALv2 to AGPL-3.0 (open-core).** The project is now
  under the GNU Affero General Public License v3, with a paid commercial license
  retained for proprietary/closed-source use without AGPL obligations. The
  bundled `omnivoice/` TTS model package stays Apache-2.0 upstream
  (AGPL-compatible). Manifests declare `AGPL-3.0-only`; the in-app Commercial
  License copy and README are updated, and the old "converts to Apache 2.0 after
  two years" FAQ is removed. In-app commercial-license strings are translated
  across all 20 locales. (#292)

### CI

- **macOS Intel (x86_64) build target reinstated** on `macos-15-intel`, so Intel
  Mac users get installers again. (#342)
- **Docker Hub publishing.** Images now also publish to Docker Hub
  (`palashdeb/omnivoice-studio`), with the Docker Hub overview maintained in-repo
  and auto-synced from `main` (sync is non-fatal so it can't redden a build).
  (#375, #410, #414)
- **Docs-drift guard.** A daily job compares the canonical feature inventory
  against README / docs / registries to catch stale docs. (#353)
- **Security scans never cancel on `main`,** so merge trains no longer leave red
  ✗ on intermediate commits. (#340)

## [0.3.5] — 2026-06-03

### Fixed
- **Speaker diarization failed on PyTorch ≥ 2.6** (`Weights only load failed …
  Unsupported global: torch.torch_version.TorchVersion`) even with the pyannote
  license accepted. PyTorch 2.6 made `torch.load` default to
  `weights_only=True`, whose secure unpickler rejects the pyannote checkpoint's
  metadata globals. The diarization loader now registers the same safe-globals
  allowlist the WhisperX VAD load already uses, so the secure load succeeds.
  (#270)

## [0.3.4] — 2026-06-03

### Fixed
- **Transcription on Windows + NVIDIA failed with `Could not locate
  cudnn_ops_infer64_8.dll`.** WhisperX/faster-whisper need cuDNN 8 (via
  CTranslate2); when the side-loaded `cudnn8_compat` libs are missing, the
  **PyTorch Whisper** backend (Settings → Models) now works as a drop-in
  fallback — it builds its own transformers pipeline on PyTorch's cuDNN-9
  stack, with no CTranslate2/cuDNN-8 dependency and no
  `OMNIVOICE_PRELOAD_TTS_ASR=1` required. (#255)

## [0.3.3] — 2026-06-03

### Fixed
- **Settings → About showed the wrong architecture in the Docker/web build.**
  The "Architecture" row rendered the *client browser's* platform
  (`navigator.platform` → e.g. "Win32"); it now reports the **server's** CPU
  architecture from the backend (`platform.machine()`), correct for both the
  desktop app and Docker. The blank version/GPU/RAM/VRAM in the same report
  were the loopback-gate 403s already fixed in v0.3.2. (#262)

### CI
- The release SHA-256 checksum step no longer uses `mapfile` (a bash 4+
  builtin) — it broke on the macOS runner's bash 3.2 and dropped the macOS
  `SHA256SUMS` for v0.3.1/v0.3.2. Now portable to bash 3.2.

## [0.3.2] — 2026-06-03

### Fixed
- **"Loopback origin required" all over the Docker UI** (and a blank version).
  The `/system/*` and `/api/settings/*` routes are restricted to a loopback
  origin, but Docker's NAT makes every request look non-loopback, so the gate
  403'd the operator out of the admin UI — including `/system/info` (blanking
  the version) and HF-token entry. The Docker image now runs with
  `OMNIVOICE_SERVER_MODE=1`, which relaxes the gate for the headless
  deployment; exposure is governed by the `-p` port mapping plus the optional
  share PIN. Desktop builds are unaffected — their loopback boundary (and the
  denial of admin routes to LAN share guests) is unchanged. (#261)

## [0.3.1] — 2026-06-03

First tagged build of the 0.3 line off `main` — it ships the accumulated
`[0.3.0]` work below plus the fixes here. (The `[0.3.0]` milestone heading is
kept for the qualitative "actually useful" release.)

### Fixed
- **Voice-clone / export download crashed in the Docker & browser build** with
  `TypeError: Cannot read properties of undefined (reading 'invoke')`. The
  export button called the Tauri save dialog unconditionally; outside the
  desktop shell it now falls back to a standard browser download of the file
  served at `/audio/<path>`. (#256)
- **Docker container showed no version** (a dash) in Settings → About, and the
  desktop-only update-channel toggle appeared in the web build. The running
  version is now read from the backend (`/system/info` `app_version`, `/health`
  `version`); the updater UI is hidden outside Tauri. Also corrected the
  version-check command in the Docker docs (`omnivoice`, not
  `omnivoice-studio`). (#249)
- **Transcription failures were masked** by a generic "Transcribe stream
  dropped" message. The transcribe SSE stream now surfaces the real, sanitized
  cause (with an actionable hint) instead of silently dropping when model load
  or VRAM offload fails. (#255)

## [0.3.0] — Unreleased

### Added
- **Frameless dictation widget.** Global dictation upgraded from an in-app FAB to a true OS-level floating widget that hovers over any application. Transparent, decorations-free, always-on-top secondary Tauri window activated by `⌘+⇧+Space`. Auto-hides 2.5 s after a successful paste.
- **Standalone `CaptureWidget` component.** Refactored `CaptureButton` into `CaptureWidget`, running on an isolated route (`/?window=widget`).
- **Social preview image.** Added `social-preview.png` for GitHub SEO.

### Changed
- **README overhaul.** Compact 3-column feature grid, reorganized Quickstart (one-command install, Docker, Desktop App tips), updated comparison table, roadmap, and footer CTA.
- **Docker Compose profiles are mutually exclusive.** CPU service now requires `--profile cpu` (was the implicit default). Prevents port 3900 conflict when running `--profile gpu`. Usage: `docker compose --profile cpu up` or `docker compose --profile gpu up`.

### Fixed
- **Docker GPU detection false negative.** Preflight reported "No compatible GPU detected" inside Docker containers because `nvidia-smi` isn't present in the PyTorch base image. The GPU probe now falls back to `torch.cuda.is_available()` and `torch.cuda.get_device_name()`, correctly showing CUDA as available in containerized deployments.

---

## [0.2.6] — Unreleased

### License
- **Relicensed Studio under [Functional Source License (FSL-1.1-ALv2)](https://fsl.software/).** Free for personal, educational, internal-team, and non-commercial use. Each release converts automatically to Apache License, Version 2.0 on the second anniversary of its publication.
- The bundled `omnivoice/` Python TTS model package remains separately licensed under Apache 2.0 by its upstream authors — not relicensed here.
- In-app **Commercial License** page no longer publishes pricing tiers. Pricing is being finalized; the page now invites quote requests and links the FSL terms.

### Added
- **Single-instance enforcement.** Launching a second copy now focuses the existing window instead of starting a second backend that races for port 3900. Powered by `tauri-plugin-single-instance`.
- **Close-to-tray.** Clicking the window X (or `Cmd+W` on macOS) now hides the window and keeps the backend + tray menu alive. The tray "Quit" item is the only path that fully exits and shuts down the Python backend (cleanup moved to `RunEvent::ExitRequested`).
- **Recording-state tray icon.** Tray icon flips to a red-dot variant while a dictation recording is active and reverts when it stops or errors out.
- **Customizable global dictation hotkey.** New **Settings → Capture** tab. Record any modifier-plus-key combo, save it, and it's persisted in `config.json` and re-registered on every launch. Failed registrations (combo already taken by the OS) roll back to the previously-working binding instead of leaving the user with no shortcut.
- **WebSocket-final dictation path.** Capture now treats the streaming `final` message as the source of truth and skips the duplicate HTTP `POST /transcribe` that used to run on every dictation. Audio is transcribed once instead of twice — typical dictation latency roughly halved. New EOF text-frame protocol (server also accepts an empty binary frame as EOF). HTTP POST kept as fallback for WS error / timeout / WS-never-opened.
- **Chunk queueing during WS handshake.** The first 250 ms of audio is no longer dropped from the server's `final` transcript. `MediaRecorder` chunks captured while the WebSocket is still in `CONNECTING` state are queued and drained in `ws.onopen`.

### Changed
- **Docker default bind is loopback.** `docker-compose.yml` now publishes `127.0.0.1:3900:3900` instead of `3900:3900` — the API is no longer reachable from the LAN out of the box. To expose it deliberately, change the mapping to `0.0.0.0:3900:3900`. README documents the trade-off and recommends a reverse proxy with auth (Caddy `basic_auth`, nginx + htpasswd, Tailscale) for any non-loopback exposure.
- **Donate page trimmed.** Removed Patreon and the Bitcoin / Ethereum / Solana cryptocurrency cards. Removed the bundled `qrcode.react` dependency. The "Commercial License" CTA moves from the bottom of the page to the top-right of the page header.
- **WS dictation hostname** now derived from the configured `API_BASE` instead of a hardcoded `localhost:3900`, so deployments behind reverse proxies route correctly.
- **HTTP POST fallback timeout** scales with recording length (`max(15s, recordedMs + 10s)`) so long-form dictations don't trip the fallback and run the model twice.

### Fixed
- **Backend was killed on every window close** even if the user only intended to dismiss the window. Backend shutdown now fires only on real-quit (`RunEvent::ExitRequested`), not on the close-to-hide path.
- **Hotkey rollback.** `set_dictation_shortcut` previously left the user with no global shortcut if `register(new)` failed after `unregister(old)` succeeded. The previous binding is now restored on failure.
- **WebSocket dictation pipeline lost the first audio chunk.** `MediaRecorder` was started before the WebSocket finished its handshake, so the first 250 ms chunk — which carries the WebM EBML header — was dropped from the WS stream. Every subsequent server-side ffmpeg conversion then failed with `exit status 183` ("Invalid data found when processing input"), partials never appeared, and the HTTP fallback only fired after the full timeout. The WebSocket is now constructed before the recorder, every chunk is queued through `wsPendingRef` until `ws.onopen` drains it, and a server `error` message (or unexpected `onclose` after the recorder has stopped) fires the HTTP fallback immediately instead of waiting out the timeout.
- **Microphone access prompt on macOS.** Added an `Info.plist` with `NSMicrophoneUsageDescription` (and `NSCameraUsageDescription` for forward-compat) so getUserMedia no longer fails silently on macOS 10.14+ TCC. Tauri's bundler auto-merges the file at bundle time. Mic-denial toasts now also include platform-specific recovery hints (Settings paths for macOS/Windows, audio-group check for Linux).

### Infrastructure
- **uv bundled per-platform.** Release installers now ship the `uv` binary as a Tauri sidecar (`bundle.externalBin`). First launch no longer requires network access for the uv-download step — bootstrap uses the bundled binary directly. Adds ~12-15 MB per platform installer; falls back to PATH lookup, then standalone download, when the bundled file isn't present (dev builds, future targets). Pinned at `UV_VERSION = "0.11.7"`; bump the constant in [lib.rs](frontend/src-tauri/src/lib.rs) and the matching env var in [release.yml](.github/workflows/release.yml) together to refresh.
- **ffmpeg fetch removed from Tauri bootstrap.** The redundant download from `eugeneware/ffmpeg-static` (saved to `app_data/bin/`) was never used by the backend, which already resolves ffmpeg via `imageio_ffmpeg.get_ffmpeg_exe()` from the pip wheel pulled by `uv sync`. Net effect: one fewer first-run network round-trip, one fewer splash-screen stage, and the splash no longer shows the misleading "Downloading ffmpeg…" line.
- **CI cross-platform check.** PRs now run `cargo check` against the Tauri shell on macOS (Apple Silicon), Windows, and Linux in parallel — surfaces platform-specific Rust regressions before tag push without paying the full ~15 min/platform tauri-bundle cost (full bundling stays in `release.yml` on tag push).
- **Release notes from CHANGELOG.** `release.yml` now extracts the matching `## [X.Y.Z]` section from `CHANGELOG.md` and uses it as the GitHub Release body, replacing the prior placeholder "Auto-generated release. See commit log for changes."
- **Tests:** `tests/test_capture_ws.py` (3 cases) covers the EOF text-frame, empty-binary-frame, and legacy disconnect-finalize paths for `/ws/transcribe`.

### Internal
- New Tauri commands: `quit_app`, `set_tray_recording`, `get_dictation_shortcut`, `set_dictation_shortcut`.
- New Tauri state: `AppFlags { quitting }`, `TrayHandle { tray }`, `DictationShortcutState { current }`.
- New deps: `tauri-plugin-single-instance` 2.x, `tauri/image-png` feature flag (enables `Image::from_bytes` for in-memory tray-icon swap).

---

## [0.2.5] — 2026-04-29

Region selector, realtime download speed, retry buttons, recheck top-right, HF mirror support, splash bootstrap-log backfill. See git log `v0.2.4..v0.2.5` for the full set.

## Earlier releases

See [GitHub Releases](https://github.com/debpalash/VoiceStudio/releases) for prior versions.
