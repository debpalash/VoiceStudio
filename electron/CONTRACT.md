# Electron shell — internal contracts (read before editing)

## Same-origin API rule

Renderer HTTP requests never call `http://127.0.0.1:3900` directly (CORS). They use
app-relative `/api/...` paths:

- dev: the electron-vite renderer dev server (port 3902) proxies `/api` → backend.
- prod: the renderer is served from the privileged `app://voicestudio/` scheme
  and main's `protocol.handle('app', …)` proxies `/api/*` to the backend with
  `net.fetch` (streaming bodies pass through; Range headers forwarded).
  So `API_BASE = '/api'` in the renderer, and `/api/audio/<file>` is a valid
  `<audio src>`.

`apiJson()` arguments are backend paths. Most are unprefixed (`/engines`), while the
shared settings and MCP routers intentionally retain their backend `/api/...` prefix.
Those calls therefore appear as `/api/api/settings/...` in renderer network tools: the
first `/api` is the desktop transport prefix removed by the dev/app protocol proxy; the
second belongs to the backend route. Do not collapse the pair in the API client.

Live dictation uses a WebSocket, which cannot pass through the app protocol HTTP handler. Native sessions use the backend URL supplied by the preload status bridge, with the exact `app://voicestudio` origin accepted by the backend. Browser development uses `/api/ws/transcribe`; the dev proxy removes only its own exact localhost origin on WebSocket upgrades. Other origins remain subject to backend validation.

## Backend process (main)

- Port: `OMNIVOICE_PORT` env or 3900. Base `http://127.0.0.1:<port>`.
- Attach-or-spawn: probe `GET /system/info`; if it answers (JSON with
  `app_version`), attach (managed=false). Else spawn.
- Spawn (dev, repo checkout): cwd = repo root (`../` from electron/), argv
  `uv run --project <root> uvicorn main:app --app-dir backend --host 127.0.0.1 --port <port>`;
  fallback if `uv` is missing: `<root>/.venv/Scripts/python.exe` (win) or
  `<root>/.venv/bin/python` with `-m uvicorn main:app --app-dir backend ...`.
- Spawn (packaged): resources dir holds `backend/`, `omnivoice/`,
  `pyproject.toml`, `uv.lock`, `README.md` and `LICENSE`; use `uv run --project <resources> ...`.
- Env for the child: PYTHONUNBUFFERED=1, PYTHONUTF8=1,
  OMNIVOICE_DESKTOP_CONTAINED=1 (arms the stdin-EOF parent watchdog),
  OMNIVOICE_PORT, FOR_DISABLE_CONSOLE_CTRL_HANDLER=1 (win), and on Windows
  TORCHDYNAMO_DISABLE=1, HF_HUB_DISABLE_SYMLINKS=1, HF_HUB_DISABLE_SYMLINKS_WARNING=1.
  Remove PYTHONHOME / PYTHONPATH from the child env.
- stdio: ['pipe','pipe','pipe'] — stdin MUST stay open (never write, never end)
  until quit; closing it is the liveness signal. `windowsHide: true`.
- Readiness: poll `/system/info` every 500 ms, budget 300 s
  (OMNIVOICE_STARTUP_BUDGET_S). Then poll every 2 s as a supervisor.
- Exit code 78 = port in use (stage `port_in_use`), not a crash.
- Quit: Windows `taskkill /pid <pid> /T /F`; POSIX spawn `detached: true` and
  `process.kill(-pid, 'SIGTERM')`, SIGKILL after 2 s; then end stdin.

## Renderer module API (lib + hooks) — implemented by the data-layer task,

## consumed by the UI task. Names/paths are fixed.

- `@/lib/api/client.ts`: `API_BASE='/api'`; `class ApiError extends Error { status:number; detail:string; payload:ApiErrorPayload|null }`;
  `apiFetch(path, init?)` (throws ApiError on !ok; `path` is relative to API_BASE);
  `apiJson<T>(path, init?)`; `audioUrl(filename)` → `/api/audio/<filename>`;
  `profileAudioUrl(id)` → `/api/profiles/<id>/audio`.
- `@/lib/api/generate.ts`: `generateClone(input: CloneGenerateInput, opts?: { signal?: AbortSignal; onProgress?: (pct: number|null) => void }): Promise<GenerateResult>`;
  `sanitizeInstruct(free: string): { instruct: string; unsupported: string[]; duplicates: string[]; conflicts: string[] }` (port of electron/src/shared/utils/voiceInstruct.js buildDesignInstruct with empty vdStates);
  `CLONE_MAX_SECONDS = 15`, `REF_HARD_MAX_SECONDS = 75`.
- `@/lib/api/profiles.ts`: `listProfiles()`, `createCloneProfile({ name, refAudio, refAudioName, refText, instruct, language })`, `deleteProfile(id)`.
- `@/lib/api/history.ts`: `listHistory()`, `clearHistory()`, `deleteHistoryItem(id)`, `setHistoryStarred(id, starred)`.
- `@/lib/api/engines.ts`: `getEngines()`, `getSystemInfo()`.
- `@/lib/api/audio.ts`: `cleanAudio(blob, filename): Promise<File>` (POST /clean-audio, field `audio`, honours X-Clean-Filename).
- `@/lib/query.ts`: `queryClient`, `queryKeys = { profiles:['profiles'], history:['history'], engines:['engines'], systemInfo:['system','info'] }`.
- `@/lib/languages.ts`: `LANGUAGES: string[]` (bundled list, index 0 = 'Auto'), `POPULAR_LANGUAGES: string[]`, `TAGS: string[]` (expression tokens).
- `@/lib/store/clone-settings.ts` (TanStack Store, persisted to localStorage `voicestudio.clone.settings.v1`):
  `interface CloneSettings { text; language; refText; instruct; steps; cfg; speed; tShift; posTemp; classTemp; layerPenalty; denoise; postprocess; duration; showOverrides; selectedProfileId: string|null; autoPlay: boolean }`,
  `DEFAULT_CLONE_SETTINGS`, `cloneSettingsStore`, `useCloneSetting(key)`, `useCloneSettings()`, `setCloneSetting(key, value)`, `patchCloneSettings(partial)`, `resetOverrides()`.
- `@/lib/store/reference.ts` (not persisted): `interface ReferenceState { file: File|null; durationSeconds: number|null; objectUrl: string|null }`, `useReference()`, `setReferenceFile(file: File|null): Promise<{ ok: boolean; durationSeconds: number|null; tooLong: boolean }>` (probes duration; clears selectedProfileId when a file is set).
- `@/lib/store/output.ts`: `interface OutputState { result: GenerateResult|null; objectUrl: string|null; text: string }`, `useLatestOutput()`, `setLatestOutput(result, text)`.
- `@/lib/audio/playback.ts`: `claimPlayback(stop: () => void, source: string): () => void`, `stopActivePlayback()`, `usePlaybackSource(): string|null`, `playBlob(blob, source): Promise<void>` (plays via a hidden <audio>, claims the slot).
- `@/hooks/use-backend-status.ts`: `useBackendStatus(): BackendStatus` (useSyncExternalStore over window.voicestudio; safe fallback when the bridge is missing, e.g. vitest).
- `@/hooks/use-generate.ts`: `useGenerateClone(): { generate(): Promise<void>; cancel(): void; isGenerating: boolean; elapsedSeconds: number; progress: number|null }` (validation toasts via sonner + i18next; routing/dropped toasts; sets output store; invalidates history; autoplay via playBlob when settings.autoPlay).
- `@/hooks/use-recording.ts`: `useRecording(onRecorded: (file: File) => void): { isRecording; isStarting; isCleaning; seconds: number; inputs: MediaDeviceInfo[]; selectedInputId: string; setSelectedInputId; channelMode: 'auto'|'mono'|'stereo'; setChannelMode; level: number; start(): Promise<void>; stop(): void }`.
- `@/hooks/use-profiles.ts`: `useProfiles()` (react-query, `Profile[]`), `useCreateCloneProfile()`, `useDeleteProfile()` (mutations; invalidate profiles).
- `@/hooks/use-history.ts`: `useHistory()`, `useDeleteHistoryItem()`, `useClearHistory()`, `useToggleStarred()`.
- `@/hooks/use-engines.ts`: `useEngines(): { data?: EnginesResponse; anyTtsReady: boolean; activeTts: EngineBackend|null; isLoading: boolean }`.
- Toasts: `import { toast } from 'sonner'`. Strings: `i18next.t('...')` / `useTranslation()`; keys live in `src/renderer/src/i18n/locales/en.json` — add keys there, never hardcode UI text.

## Tooling

- `bun run typecheck` (tsgo, TypeScript 7), `bun run lint` (vp lint / oxlint), `bun run format` (vp fmt / oxfmt), `bun run test` (vp test / vitest, jsdom), `bun run build` (electron-vite), `bun run dev`.
- Do NOT add dependencies. If one is truly needed, report it instead.

- Distribution commands run `tests/packaging-contract.mjs` before packaging: built main/preload syntax, required Python resources and app-version source. `node tests/packaged-smoke.mjs` (from repo root: `node electron/tests/packaged-smoke.mjs`) checks the Windows artifact against an available backend; this does not prove fresh-machine Python bootstrap.

- Packaged runtime: `backend.setupRuntime()` is a trusted main-frame-only explicit install action. Stages `setup_required` and `installing` expose setup and cancellation UI; normal `restart()` never starts downloads. See `docs/electron-runtime.md`.

- Distribution builds require Cargo and the selected Rust target. `afterPack` compiles `native/desktop-bridge` with its lockfile, places the executable in `resources/native`, and signs the Windows helper through electron-builder. No build tool is needed by installed users.
