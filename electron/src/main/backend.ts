import { decideMissingRuntime, runtimeWasInstalled } from './runtime-repair';
import { windowsRocmInstalled, windowsRocmRequested } from './runtime-windows-rocm';
import { runtimeProjectReady } from './runtime-readiness';
import { runRuntimeProbe } from './runtime-probe';
import { RuntimeLaunchRefusedError } from './runtime-launch-refusal';
import { pypiFilesHost } from './pypi-mirror';
import {
  installRuntime,
  MISSING_SYSTEM_LIBRARY_CODE,
  OUTDATED_SYSTEM_LIBRARY_CODE,
  RUNTIME_FILES_LOCKED_CODE,
  promoteLegacyRuntimeCaches,
  runtimeDependenciesReady,
  runtimeDownloadHosts,
  runtimeInstallInterrupted,
  runtimePython,
  runtimeUvCache,
  stageRuntimeSources,
  type RuntimeRegion,
} from './runtime-project';
import { collectHostSnapshot, freeGb, type HostSnapshot } from './host-snapshot';
import { runtimeSetupSupported } from './distribution';
import { asciiSafePthFiles } from './pth-ascii';
import { devProbeStamped, writeDevProbeStamp } from './dev-probe-stamp';
import { CrashJournal } from './crash-journal';
import { nativeFaultSummary } from '../shared/utils/crashReport';
import {
  backendStartupFailure,
  isBrokenEnvironmentOutput,
  windowsLoadBlock,
  type WindowsLoadBlock,
} from '../shared/utils/backendOutput';
import { availableBackendPort } from './backend-port';
import { scopedStorage, scopedStorageEnv, type ScopedStorage } from './storage-scope';
import { legacyStorageEnv } from './legacy-storage';
import { spawn, spawnSync, type ChildProcess, type StdioOptions } from 'node:child_process';
import { EventEmitter } from 'node:events';
import {
  accessSync,
  constants,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { access, rm, stat } from 'node:fs/promises';
import { cpus, homedir, totalmem } from 'node:os';
import { basename, dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { app } from 'electron';
import { APP_ORIGIN } from './protocol';
import type {
  BackendConnection,
  BackendStage,
  BackendStatus,
  NativeCrashRecord,
  RemoteBackendProbe,
} from '../preload/index.d';
import {
  loadRemoteBackend,
  probeRemoteBackend,
  remoteWebSocketUrl,
  saveRemoteBackend,
  type RemoteSession,
} from './remote-backend';
import { toolSearchDirs, toolSearchDirsAsync } from './tool-path';
import { SetupProgressTracker, cleanProcessLine } from './setup-progress';
import { SetupDownloadReporter, inFlightDownloadBytes } from './setup-downloads';
import { scrubText } from '../shared/utils/scrub';
import { DEFAULT_BACKEND_PORT } from '../../../config/app-config.mjs';

const DEFAULT_BUDGET_S = 300;
const READY_POLL_MS = 500;
/** Allow another VoiceStudio shell to take ownership without showing a false crash. */
const REPLACEMENT_ATTACH_GRACE_MS = 10_000;
const SUPERVISE_POLL_MS = 2000;
const PROBE_TIMEOUT_MS = 1500;
const SHUTDOWN_INTENT_TIMEOUT_MS = 1000;
/** Consecutive supervisor probe misses (2 s apart) before a ready backend is declared gone. */
const SUPERVISE_MISSES = 3;
/**
 * Consecutive probes that got an HTTP answer which was not a healthy /health
 * (about a minute at the supervisor's cadence) before the backend is reported
 * as unhealthy. A listener that answers is alive, so this is never `crashed`
 * and never "busy" — it is a different, accurate state, and it still clears
 * itself as soon as a probe comes back healthy.
 */
const SUPERVISE_REJECTIONS = 15;
const REMOTE_AUTH_CHECK_TIMEOUT_MS = 5000;
/** Healthy supervise ticks (2 s apart) between credential re-checks of an auth_required remote. */
const AUTH_RECHECK_TICKS = 15;
/**
 * Stages a later successful /health probe must retire back to `ready` (#2430).
 * `unresponsive` is a live-but-busy backend, not a failure: the supervisor
 * already proved the process is alive, so the health loop owns clearing it.
 */
const RECOVERABLE_STAGES: ReadonlySet<BackendStage> = new Set<BackendStage>([
  'failed',
  'unresponsive',
]);
const LOG_RING_LINES = 200;
const LOG_TAIL_LINES = 40;
/** EX_CONFIG (sysexits.h): backend/main.py exits with it when the port is taken (#1223). */
const EXIT_PORT_IN_USE = 78;

/**
 * The desktop shell starts `python -m uvicorn`, not backend/main.py's
 * `__main__`, so main.py's own bind diagnostics (exit 78 / permission-denied
 * text) never run. uvicorn instead exits 3 after logging asyncio's
 * "[Errno N] error while attempting to bind on address ..." line. The errno
 * is locale-independent, so classify on it (#3020).
 */
export function bindFailureFromLog(text: string): 'in_use' | 'denied' | null {
  const match = /\[Errno (-?\d+)\] error while attempting to bind on address/.exec(text);
  if (!match) return null;
  const errno = Number(match[1]);
  if ([48, 98, 10048].includes(errno)) return 'in_use';
  if ([13, 10013].includes(errno)) return 'denied';
  return null;
}
const POSIX_SIGKILL_AFTER_MS = 2000;
const TAURI_APP_ID = 'com.debpalash.omnivoice-studio';
const RUNTIME_LOCATION_FILE = 'runtime-location.json';
const RUNTIME_PREFERENCES_FILE = 'runtime-preferences.json';
const RUNTIME_REGIONS = new Set<RuntimeRegion>(['auto', 'global', 'china', 'russia', 'restricted']);

/** Pipe closures emitted while a child is exiting are lifecycle signals, not failures. */
export function isExpectedPipeClose(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const code = (error as NodeJS.ErrnoException).code;
  return code === 'EPIPE' || code === 'ECONNRESET' || code === 'ERR_STREAM_PREMATURE_CLOSE';
}

interface RuntimeLocationConfig {
  root: string;
  owned: boolean;
  /** Chosen during first-run setup: data, models and temp live beside the runtime. */
  scoped: boolean;
}

function runtimePreferencesPath(): string {
  return join(app.getPath('userData'), RUNTIME_PREFERENCES_FILE);
}

function loadRuntimeRegion(): RuntimeRegion {
  try {
    const value = JSON.parse(readFileSync(runtimePreferencesPath(), 'utf8')) as {
      region?: unknown;
    };
    return typeof value.region === 'string' && RUNTIME_REGIONS.has(value.region as RuntimeRegion)
      ? (value.region as RuntimeRegion)
      : 'auto';
  } catch {
    return 'auto';
  }
}

const UVICORN_ARGS = ['uvicorn', 'main:app', '--app-dir', 'backend', '--host', '127.0.0.1'];

export type StatusListener = (status: BackendStatus) => void;

export function resolvePort(): number {
  const raw = Number(process.env.OMNIVOICE_PORT);
  return Number.isInteger(raw) && raw > 0 && raw < 65536 ? raw : DEFAULT_BACKEND_PORT;
}

/** Hosts at or below this are "low-spec": a cold backend import is disk/CPU-bound. */
const LOW_SPEC_CORES = 4;
const LOW_SPEC_MEMORY_BYTES = 8 * 1024 ** 3;
const LOW_SPEC_BUDGET_FACTOR = 2;
/** A backend that keeps talking may be given at most this many budgets in total. */
const MAX_BUDGET_EXTENSIONS = 3;

/**
 * The default readiness budget, doubled on a small host (#2445). A machine with
 * no dedicated GPU is usually also short on cores and RAM, and its first start
 * after an install is the slowest: antivirus scans the freshly written native
 * libraries while a handful of cores import torch and the whole backend. That
 * is slow, not broken, so it must not be failed at the budget sized for a
 * workstation. An explicit `OMNIVOICE_STARTUP_BUDGET_S` always wins. This only
 * stretches a timeout - it never changes what a feature does - so it is not a
 * cross-platform behavior difference.
 */
export function defaultStartupBudgetS(cores = cpus().length, memoryBytes = totalmem()): number {
  const small = cores <= LOW_SPEC_CORES || memoryBytes <= LOW_SPEC_MEMORY_BYTES;
  return small ? DEFAULT_BUDGET_S * LOW_SPEC_BUDGET_FACTOR : DEFAULT_BUDGET_S;
}

function startupBudgetMs(): number {
  const raw = Number(process.env.OMNIVOICE_STARTUP_BUDGET_S);
  return (Number.isFinite(raw) && raw >= 0 ? raw : defaultStartupBudgetS()) * 1000;
}

/**
 * When the readiness wait should give up. `base` is the plain budget; a backend
 * that has printed something within the last half-budget is alive and
 * progressing (migrations, model-directory scans), so the deadline slides to
 * stay half a budget past its last output, never beyond `MAX_BUDGET_EXTENSIONS`
 * budgets in total. A silent or wedged backend still fails at `base`.
 */
export function readinessDeadline(
  startedAt: number,
  budgetMs: number,
  lastOutputAt: number,
): number {
  const base = startedAt + budgetMs;
  if (lastOutputAt <= 0) return base;
  const ceiling = startedAt + budgetMs * MAX_BUDGET_EXTENSIONS;
  return Math.max(base, Math.min(ceiling, lastOutputAt + budgetMs / 2));
}

/**
 * `OMNIVOICE_BACKEND_CMD` override, mirroring the Tauri shell: a JSON array when
 * it starts with `[` (paths with spaces survive), else whitespace-split.
 */
export function parseBackendCmdOverride(raw: string | undefined): string[] | null {
  const text = raw?.trim() ?? '';
  if (!text) return null;
  let argv: unknown;
  if (text.startsWith('[')) {
    try {
      argv = JSON.parse(text);
    } catch {
      return null;
    }
  } else {
    argv = text.split(/\s+/);
  }
  if (!Array.isArray(argv) || !argv.every((a): a is string => typeof a === 'string')) return null;
  if (argv.length === 0 || argv[0].trim() === '') return null;
  return argv;
}

/** Repo checkout in dev (`electron/..`), the bundled resources dir when packaged. */
export function backendRoot(): string {
  if (app.isPackaged) return process.resourcesPath;
  // app.getAppPath() is electron/ under `electron-vite dev|preview` but
  // electron/out when the built entry is launched directly (`electron
  // out/main/index.js`), so walk up until the repo checkout is found.
  let dir = app.getAppPath();
  for (let i = 0; i < 6; i++) {
    if (existsSync(join(dir, 'backend', 'main.py')) && existsSync(join(dir, 'pyproject.toml'))) {
      return dir;
    }
    const parent = resolve(dir, '..');
    if (parent === dir) break;
    dir = parent;
  }
  return resolve(app.getAppPath(), '..');
}

export function bundledUvPath(resourcesPath: string, platform = process.platform): string {
  return join(resourcesPath, 'tools', platform === 'win32' ? 'uv.exe' : 'uv');
}

/** Whether this host has no installable managed AI runtime. */
export function isUnsupportedPlatform(platform = process.platform, arch = process.arch): boolean {
  return platform === 'darwin' && arch === 'x64';
}

/**
 * Why this host cannot install the local runtime, if it cannot (#2598).
 *
 * An x64 process on macOS is either a real Intel Mac or the Intel build
 * running through Rosetta on Apple Silicon. Both resolve x86_64 wheels and
 * fail, but only the second has a fix on the same machine: install the
 * Apple Silicon build. Telling that user "this Intel Mac is unsupported"
 * sends them to a remote backend they do not need.
 */
export function platformSetupIssue(
  platform = process.platform,
  arch = process.arch,
  translated = app.runningUnderARM64Translation === true,
): 'unsupported_platform' | 'wrong_architecture' | undefined {
  if (!isUnsupportedPlatform(platform, arch)) return undefined;
  return translated ? 'wrong_architecture' : 'unsupported_platform';
}

function usableFile(path: string): boolean {
  try {
    if (!existsSync(path)) return false;
    const stat = statSync(path);
    if (!stat.isFile() || stat.size <= 0) return false;
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * The uv to hand the backend: the user's own non-blank pin, else the one this shell
 * located. A blank value is dropped rather than passed on, because the backend reads
 * any set value as "the shell tried and failed" instead of falling back to PATH.
 */
export function bundledUvEnv(
  pinned: string | undefined,
  locate: () => string | null,
): string | undefined {
  if (pinned?.trim()) return pinned;
  return locate() ?? undefined;
}

function findUv(): string | null {
  if (app.isPackaged && process.resourcesPath) {
    const bundled = bundledUvPath(process.resourcesPath);
    if (usableFile(bundled)) return bundled;
  }
  const names = process.platform === 'win32' ? ['uv.exe', 'uv'] : ['uv'];
  const dirs = toolSearchDirs();
  for (const dir of dirs) {
    for (const name of names) {
      const candidate = join(dir, name);
      if (usableFile(candidate)) return candidate;
    }
  }
  return null;
}

/** Off-main-thread twin of findUv() for diagnostics: a stalled PATH drive must not block the event loop. */
async function findUvAsync(): Promise<string | null> {
  const usable = async (path: string) => {
    try {
      const info = await stat(path);
      if (!info.isFile() || info.size <= 0) return false;
      await access(path, constants.X_OK);
      return true;
    } catch {
      return false;
    }
  };
  if (app.isPackaged && process.resourcesPath) {
    const bundled = bundledUvPath(process.resourcesPath);
    if (await usable(bundled)) return bundled;
  }
  const names = process.platform === 'win32' ? ['uv.exe', 'uv'] : ['uv'];
  for (const dir of await toolSearchDirsAsync()) {
    for (const name of names) {
      const candidate = join(dir, name);
      if (await usable(candidate)) return candidate;
    }
  }
  return null;
}

function venvPython(root: string): string {
  return process.platform === 'win32'
    ? join(root, '.venv', 'Scripts', 'python.exe')
    : join(root, '.venv', 'bin', 'python');
}

interface SpawnPlan {
  argv: string[];
  cwd: string;
}

interface LegacyInstallConfig {
  install_mode?: string;
  installMode?: string;
  env_dir?: string | null;
  envDir?: string | null;
  portable_dir?: string | null;
  portableDir?: string | null;
}

function legacyTauriRoots(): string[] {
  if (process.platform === 'win32') {
    return [process.env.LOCALAPPDATA, process.env.APPDATA]
      .filter((value): value is string => Boolean(value))
      .map((root) => join(root, TAURI_APP_ID));
  }
  if (process.platform === 'darwin')
    return [join(homedir(), 'Library', 'Application Support', TAURI_APP_ID)];
  return [
    join(process.env.XDG_DATA_HOME || join(homedir(), '.local', 'share'), TAURI_APP_ID),
    join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), TAURI_APP_ID),
  ];
}

/** Candidate Tauri project roots, including its configured custom/portable location. */
export function legacyTauriRuntimeProjects(): string[] {
  const projects: string[] = [];
  for (const root of legacyTauriRoots()) {
    let config: LegacyInstallConfig = {};
    try {
      config = JSON.parse(readFileSync(join(root, 'config.json'), 'utf8')) as LegacyInstallConfig;
    } catch {
      /* A default install may not have written configuration yet. */
    }
    const mode = config.install_mode ?? config.installMode;
    const portable = config.portable_dir ?? config.portableDir;
    const custom = config.env_dir ?? config.envDir;
    if (mode === 'portable' && portable?.trim())
      projects.push(join(portable.trim(), 'env', 'project'));
    else if (custom?.trim()) projects.push(join(custom.trim(), 'project'));
    projects.push(join(root, 'project'));
  }
  return [...new Set(projects.map((project) => resolve(project)))];
}

function defaultRuntimeRoot(): string {
  return join(app.getPath('userData'), 'runtime');
}

function storedRuntimeLocation(): RuntimeLocationConfig | null {
  try {
    const parsed = JSON.parse(
      readFileSync(join(app.getPath('userData'), RUNTIME_LOCATION_FILE), 'utf8'),
    ) as { root?: unknown; owned?: unknown; scoped?: unknown };
    return typeof parsed.root === 'string' && isAbsolute(parsed.root)
      ? { root: resolve(parsed.root), owned: parsed.owned === true, scoped: parsed.scoped === true }
      : null;
  } catch {
    return null;
  }
}

function storedRuntimeRoot(): string | null {
  return storedRuntimeLocation()?.root ?? null;
}

function writeRuntimeLocation(
  root: string,
  owned: boolean,
  scoped: boolean = storedRuntimeLocation()?.scoped === true,
): void {
  const locationFile = join(app.getPath('userData'), RUNTIME_LOCATION_FILE);
  mkdirSync(dirname(locationFile), { recursive: true });
  writeFileSync(locationFile, JSON.stringify({ root: resolve(root), owned, scoped }), 'utf8');
}

/** The recorded first-run location whose data/models/temp follow the runtime, if any. */
function scopedRuntimeRoot(): string | null {
  const stored = storedRuntimeLocation();
  return stored?.scoped && !samePath(stored.root, defaultRuntimeRoot()) ? stored.root : null;
}

function selectedRuntimeRoot(parent: string): string {
  const selected = resolve(parent);
  return basename(selected).toLocaleLowerCase('en-US') === 'voicestudio'
    ? selected
    : join(selected, 'VoiceStudio');
}

/** File-manager metadata a new folder can hold; it is not another installation's data. */
const FOLDER_METADATA = new Set(['.ds_store', '.localized', 'desktop.ini', 'thumbs.db']);

/**
 * Whether a selected runtime root holds files Electron must not take over. The
 * folder picker can create the destination, so an existing empty folder is a
 * new destination rather than another installation.
 */
function holdsUnownedFiles(root: string): boolean {
  try {
    return readdirSync(root, { withFileTypes: true }).some(
      (entry) => !entry.isFile() || !FOLDER_METADATA.has(entry.name.toLowerCase()),
    );
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ENOENT';
  }
}

/** The web UI build packaged with this app version (served to LAN devices). */
export function bundledWebUiPath(): string {
  return join(backendRoot(), 'frontend', 'dist');
}

function samePath(left: string, right: string): boolean {
  const normalizedLeft = resolve(left);
  const normalizedRight = resolve(right);
  return process.platform === 'win32'
    ? normalizedLeft.toLocaleLowerCase('en-US') === normalizedRight.toLocaleLowerCase('en-US')
    : normalizedLeft === normalizedRight;
}

/** A source checkout that cannot launch, and the setup screen that explains why. */
export interface SpawnPlanError {
  error: string;
  /** Set when no local setup can succeed on this host (Intel Mac, #3584). */
  setupIssue?: 'unsupported_platform';
}

export async function resolveSpawnPlan(
  port: number,
  packagedProject?: string,
  hostIssue: ReturnType<typeof platformSetupIssue> = platformSetupIssue(),
): Promise<SpawnPlan | SpawnPlanError> {
  const root = backendRoot();
  const override = parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD);
  if (override) return { argv: override, cwd: root };
  if (app.isPackaged) {
    const project = packagedProject ?? join(defaultRuntimeRoot(), 'project');
    // Heals runtimes installed before #1783 was fixed without a repair run.
    await asciiSafePthFiles(join(project, '.venv')).catch(() => []);
    return {
      argv: [runtimePython(project), '-m', ...UVICORN_ARGS, '--port', String(port)],
      cwd: project,
    };
  }
  // Setup is explicit: `uv run` can sync gigabytes inside the health-check
  // deadline and repeat that download after every restart (#2184).
  const portArg = ['--port', String(port)];
  // PyTorch publishes no Intel-Mac wheels, so `uv sync` cannot resolve the lock
  // there and every module stays missing (#3584). Advising `setup:api` would
  // only repeat that failure; say what does work instead.
  if (hostIssue === 'unsupported_platform') {
    return {
      error:
        'This Intel Mac cannot run the local VoiceStudio backend: PyTorch no longer publishes ' +
        'Intel-Mac wheels, so `bun run setup:api` cannot install it. Connect the app to a ' +
        'VoiceStudio backend on another computer, or run the backend on an Apple Silicon Mac, ' +
        'Windows or Linux.',
      setupIssue: 'unsupported_platform',
    };
  }
  if (hostIssue === 'wrong_architecture') {
    return {
      error:
        `This checkout runs Intel (x64) Electron under Rosetta on an Apple Silicon Mac, and Intel-Mac ` +
        `PyTorch wheels do not exist. Open a native (arm64) terminal in ${root}, delete node_modules ` +
        'and .venv, run `bun install` and `bun run setup:api` with an arm64 Bun, then start the app again.',
    };
  }
  const python = venvPython(root);
  // Only a source checkout reaches this point: an installed app returned above
  // with its app-managed runtime, which the setup screen repairs in-app (#3480).
  // Retry re-runs this check, so a finished setup needs no app restart.
  const setup = `Open a terminal in ${root}, run \`bun run setup:api\`, wait for it to finish, then press Retry.`;
  if (!existsSync(python)) {
    // Not a source checkout and not a packaged app (a copied folder, a
    // download of the wrong archive): setup:api does not exist there (#2988).
    if (!existsSync(join(root, 'pyproject.toml'))) {
      return {
        error:
          `The Python environment in ${root} is missing (no ${relative(root, python)}), and this is not a VoiceStudio source checkout. ` +
          'Install VoiceStudio from the release installer, or clone the repository and run `bun run setup:api` inside it.',
      };
    }
    return {
      error: `The Python environment in ${root} is missing (no ${relative(root, python)}). ${setup}`,
    };
  }
  // Name the import that failed (#2555): "incomplete" alone cannot tell a
  // setup that never ran from one that finished but cannot load a module.
  let failure = '';
  if (devProbeStamped(root, python)) {
    return { argv: [python, '-m', ...UVICORN_ARGS, ...portArg], cwd: root };
  }
  if (await runtimeDependenciesReady(root, (detail) => (failure = detail))) {
    writeDevProbeStamp(root, python);
    return { argv: [python, '-m', ...UVICORN_ARGS, ...portArg], cwd: root };
  }
  return {
    error:
      `The Python environment in ${root} is incomplete${failure ? `: ${failure}` : ''}. ${setup} ` +
      'If setup already finished without errors, include this message and the setup output in a bug report.',
  };
}

/** The port of the electron-vite dev renderer URL, if it is a local http URL. */
export function devRendererPort(rendererUrl: string | undefined): string | null {
  if (!rendererUrl) return null;
  try {
    const url = new URL(rendererUrl);
    return url.protocol === 'http:' && url.port ? url.port : null;
  } catch {
    return null;
  }
}

function childEnv(
  port: number,
  _region: RuntimeRegion = 'auto',
  platform = process.platform,
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...(app.isPackaged ? legacyStorageEnv(legacyTauriRoots()) : {}),
    ...process.env,
  };
  const scopedRoot = app.isPackaged ? scopedRuntimeRoot() : null;
  if (scopedRoot) {
    Object.assign(env, scopedStorageEnv(scopedRoot, env));
    for (const dir of [scopedStorage(scopedRoot).temp]) {
      try {
        mkdirSync(dir, { recursive: true });
      } catch {
        /* An unwritable folder surfaces through the setup space/permission checks. */
      }
    }
  }
  const packagedToken =
    typeof __POSTHOG_PROJECT_TOKEN__ === 'string' ? __POSTHOG_PROJECT_TOKEN__ : '';
  const packagedHost = typeof __POSTHOG_HOST__ === 'string' ? __POSTHOG_HOST__ : '';
  if (packagedToken && !env.POSTHOG_PROJECT_TOKEN) {
    env.POSTHOG_PROJECT_TOKEN = packagedToken;
  }
  if (packagedHost && !env.POSTHOG_HOST) env.POSTHOG_HOST = packagedHost;
  // The backend reports the same build commit as the app in bug reports.
  if (!env.OMNIVOICE_BUILD_COMMIT && __BUILD_COMMIT__ !== 'unknown') {
    env.OMNIVOICE_BUILD_COMMIT = __BUILD_COMMIT__;
  }
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  env.PYTHONUNBUFFERED = '1';
  env.PYTHONUTF8 = '1';
  // Arms backend/core/parent_liveness.py: stdin EOF == "the shell is gone".
  env.OMNIVOICE_DESKTOP_CONTAINED = '1';
  env.OMNIVOICE_PORT = String(port);
  // The dev renderer is served by Vite on a real port; tell the backend so
  // Settings -> Sharing reports it. Packaged builds serve app:// (no port).
  const rendererPort = devRendererPort(env.ELECTRON_RENDERER_URL);
  if (rendererPort && !env.OMNIVOICE_UI_PORT?.trim() && !env.VOICESTUDIO_UI_PORT?.trim())
    env.OMNIVOICE_UI_PORT = rendererPort;
  // #2215: the backend resolves uv as OMNIVOICE_BUNDLED_UV first and
  // `shutil.which("uv")` second. The packaged uv lives in resources/tools,
  // which is on nobody's PATH, and a GUI launch does not inherit the shell's
  // PATH either — so `which` missed a uv the shell had already located, and
  // every one-click sidecar install died at preflight with "uv was not found"
  // while the binary sat inside the app bundle. findUv() knows where to look;
  // hand the answer over instead of keeping it. An explicit override from the
  // environment still wins.
  const uv = bundledUvEnv(env.OMNIVOICE_BUNDLED_UV, findUv);
  if (uv) env.OMNIVOICE_BUNDLED_UV = uv;
  else delete env.OMNIVOICE_BUNDLED_UV;
  // #2599: LAN devices load the web UI from this backend. Serve the build
  // shipped inside this app version's resources, never a copy beside the
  // runtime project (absent on updated installs, stale after an update).
  if (app.isPackaged && process.resourcesPath && !env.OMNIVOICE_FRONTEND_DIST?.trim()) {
    env.OMNIVOICE_FRONTEND_DIST = bundledWebUiPath();
  }
  // Region defaults must leave HF_ENDPOINT unset so the backend can probe and
  // fail over. Only an explicit user endpoint may disable automatic selection.
  if (platform === 'win32') {
    env.TORCHDYNAMO_DISABLE = '1';
    env.HF_HUB_DISABLE_SYMLINKS = '1';
    env.HF_HUB_DISABLE_SYMLINKS_WARNING = '1';
    // MKL's Fortran runtime aborts the child on console CLOSE/LOGOFF events (#1153).
    env.FOR_DISABLE_CONSOLE_CTRL_HANDLER ??= '1';
  }
  return env;
}

/**
 * Capabilities only the managed backend may hold (loopback bridges into main).
 * Kept out of process.env so no other child (agent CLIs, browsers, xdg-open,
 * setup tools) can inherit them.
 */
const backendOnlyEnv: Record<string, string> = {};

export function setBackendOnlyEnv(vars: Record<string, string | undefined>): void {
  for (const [key, value] of Object.entries(vars)) {
    if (value === undefined) delete backendOnlyEnv[key];
    else backendOnlyEnv[key] = value;
  }
}

export function managedBackendSpawnOptions(
  port: number,
  region: RuntimeRegion = 'auto',
  platform = process.platform,
): { env: NodeJS.ProcessEnv; stdio: StdioOptions; drainFd: number | null } {
  const drainFd = platform === 'win32' ? null : 3;
  const env = { ...childEnv(port, region, platform), ...backendOnlyEnv };
  if (drainFd !== null) env.OMNIVOICE_DESKTOP_DRAIN_FD = String(drainFd);
  return {
    env,
    stdio: drainFd === null ? ['pipe', 'pipe', 'pipe'] : ['pipe', 'pipe', 'pipe', 'pipe'],
    drainFd,
  };
}

export type SmartAppControl = 'on' | 'evaluation' | 'off' | 'unknown';

/** Windows keeps Smart App Control in `VerifiedAndReputablePolicyState`: 0 off, 1 on, 2 evaluation. */
export function smartAppControlFromRegistry(output: string): SmartAppControl {
  const match = /VerifiedAndReputablePolicyState\s+REG_DWORD\s+0x([0-9a-f]+)/i.exec(output);
  if (!match) return 'unknown';
  return (
    ({ 0: 'off', 1: 'on', 2: 'evaluation' } as const)[Number.parseInt(match[1], 16) as 0 | 1 | 2] ??
    'unknown'
  );
}

/** Read only after a launch was already refused, so the 3 s cap never delays a healthy start. */
function readSmartAppControl(platform = process.platform): SmartAppControl {
  if (platform !== 'win32') return 'unknown';
  try {
    const result = spawnSync(
      'reg.exe',
      [
        'query',
        String.raw`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy`,
        '/v',
        'VerifiedAndReputablePolicyState',
      ],
      { encoding: 'utf8', timeout: 3000, windowsHide: true },
    );
    return smartAppControlFromRegistry(result.stdout ?? '');
  } catch {
    return 'unknown';
  }
}

/** A failed spawn names the program, not just the OS error. Windows denies a
 *  blocked executable with a bare `spawn UNKNOWN`; missing runtime-owned programs
 *  point at their install, while custom `OMNIVOICE_BACKEND_CMD` launches point
 *  at their own executable instead (#2440). When Smart App
 *  Control is on, it is named: it has no per-app exceptions, so the usual
 *  antivirus-exclusion advice cannot help (#3224). */
export function spawnFailureMessage(
  command: string,
  error: unknown,
  {
    runtimeOwned = true,
    smartAppControl,
  }: { runtimeOwned?: boolean; smartAppControl?: SmartAppControl } = {},
): string {
  const detail = errorMessage(error);
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  const launchRejected = ['ENOENT', 'UNKNOWN', 'EACCES', 'EPERM'].includes(code ?? '');
  if (!launchRejected) return `Could not start ${command}: ${detail}`;
  if (runtimeOwned && code === 'UNKNOWN') {
    const control = smartAppControl ?? readSmartAppControl();
    if (control === 'on' || control === 'evaluation') {
      return `Could not start ${command}: ${detail}. Windows Smart App Control blocked it.`;
    }
  }
  // Refused execution is not evidence of a broken installation. The renderer
  // adds its localized permission/security hint to this diagnostic.
  if (runtimeOwned && code !== 'ENOENT') return `Could not start ${command}: ${detail}`;
  return runtimeOwned
    ? `Could not start ${command}: ${detail}. Install or repair the local runtime, then restart VoiceStudio.`
    : `Could not start ${command}: ${detail}. Check that the program exists and can be launched, then try again.`;
}

function runtimeFailureMessage(error: unknown): string {
  return error instanceof RuntimeLaunchRefusedError
    ? spawnFailureMessage(error.program, error.cause)
    : errorMessage(error);
}

/** A backend that Windows stopped from loading one of its files names who did
 *  it, in words the renderer's hints recognise: Smart App Control (no per-app
 *  exceptions), an antivirus, or another application-control policy (#3209). */
export function loadBlockMessage(
  why: string,
  lastOutput: string,
  block: WindowsLoadBlock,
  smartAppControl: SmartAppControl = block === 'application_control'
    ? readSmartAppControl()
    : 'unknown',
): string {
  const who =
    block === 'antivirus'
      ? 'Your antivirus blocked it.'
      : block === 'application_control' &&
          (smartAppControl === 'on' || smartAppControl === 'evaluation')
        ? 'Windows Smart App Control blocked it.'
        : 'A Windows application control policy blocked it.';
  return (
    `Backend exited (${why}): Windows refused to load one of the engine's files. ${who}` +
    (lastOutput ? ` Last output: ${lastOutput}` : '')
  );
}

/** The startup-budget failure says what the launch actually did, so a report
 *  shows where startup stalled instead of asking for the log (#2445): the
 *  backend's last line, "no output" for a managed process that never printed,
 *  or "nothing spawned" for an attach-only wait that owns no process. */
export function startupTimeoutMessage(
  port: number,
  budgetMs: number,
  { owned, lastOutput }: { owned: boolean; lastOutput?: string },
): string {
  const base =
    `Backend did not answer on port ${port} within ${Math.round(budgetMs / 1000)} s ` +
    '(OMNIVOICE_STARTUP_BUDGET_S).';
  if (!owned) return `${base} Nothing was spawned for this attempt.`;
  const advice =
    ' Slow disks and antivirus scanning of the runtime folder are the usual cause: click Retry, ' +
    'exclude the VoiceStudio runtime folder from real-time scanning, or raise OMNIVOICE_STARTUP_BUDGET_S ' +
    '(for example 900).';
  return lastOutput
    ? `${base} Last output: ${lastOutput}${advice}`
    : `${base} It printed no output.${advice}`;
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export class BackendSupervisor extends EventEmitter<{
  status: [BackendStatus];
}> {
  private readonly crashes = new CrashJournal(
    join(app.getPath('userData'), 'backend-crashes.json'),
    __APP_VERSION__,
  );
  private readonly remotePath = join(app.getPath('userData'), 'remote-backend.json');
  private readonly configuredPort = resolvePort();
  private localPort = this.configuredPort;
  get port(): number {
    return this.localPort;
  }
  private remoteUrl = loadRemoteBackend(this.remotePath);
  private remoteSession: RemoteSession | null = null;
  private testedRemote: { url: string; session: RemoteSession | null } | null = null;

  private stage: BackendStage = 'idle';
  private managed = false;
  private message: string | undefined;
  private exitCode: number | null | undefined;
  private exitSignal: string | null | undefined;
  private startedAt = Date.now();
  private child: ChildProcess | null = null;
  private readonly log: string[] = [];
  /** Only the spawned process's own output, for quoting back in failure messages. */
  private readonly childLog: string[] = [];
  /**
   * The spawned process's stderr only.
   *
   * The backend's verdict about a failed start is printed there, while the
   * access log uvicorn writes to stdout can be the last line of a failed
   * launch — every /health poll during startup prints one. Quoting "the last
   * line" therefore reported `503 Service Unavailable` to a user whose real
   * problem was a native extension that would not load (#3058).
   */
  private readonly childErrorLog: string[] = [];
  /** When the spawned process last printed a line (0 = nothing yet this launch). */
  private lastChildOutputAt = 0;
  /** Bumped on every start/shutdown so stale poll loops and exit handlers no-op. */
  private generation = 0;
  /** A generation owns at most one health loop, even if readiness is observed twice. */
  private supervisingGeneration: number | null = null;
  /**
   * How the latest probe failed. Only `refused` — no listener, or another
   * service on the port — is evidence the backend is gone. A `timeout` means
   * the kernel accepted the connection while the event loop was blocked (a
   * long job can hold it for hours), and `rejected` means it answered unhealthy.
   */
  private lastProbeOutcome: 'ok' | 'timeout' | 'refused' | 'rejected' = 'ok';
  private shuttingDown = false;
  private diagnosis: BackendStatus['diagnosis'];
  private setupIssue: BackendStatus['setupIssue'];
  private setupRequiredGib: number | undefined;
  /** Free GiB on the drive that will hold the runtime; probed once per location during setup. */
  private runtimeFreeGib: number | undefined;
  private freeProbeRoot: string | undefined;
  private runtimeInterrupted = false;
  private repairAttempted = false;
  private runtimeRepairFailed = false;
  private setupPhase: BackendStatus['setupPhase'] = 'checking';
  private readonly setupProgress = new SetupProgressTracker();
  private installation: AbortController | null = null;
  private cleaningRuntime = false;
  private runtimeProject: string | null = null;
  private runtimeRegion: RuntimeRegion = loadRuntimeRegion();

  get baseUrl(): string {
    return this.remoteUrl || `http://127.0.0.1:${this.port}`;
  }

  get connection(): BackendConnection {
    return {
      remote: Boolean(this.remoteUrl),
      url: this.baseUrl,
      authenticated: Boolean(this.activeSession()),
    };
  }

  /** Machine and runtime facts for bug reports; works while the backend is down. */
  async hostSnapshot(): Promise<HostSnapshot> {
    // Same location resolveSpawnPlan() launches from.
    const project = app.isPackaged
      ? (this.runtimeProject ?? join(storedRuntimeRoot() ?? defaultRuntimeRoot(), 'project'))
      : backendRoot();
    return collectHostSnapshot({
      runtimeProject: project,
      runtimePython: runtimePython(project),
      uv: findUvAsync,
      gpu: async () => {
        const info = (await app.getGPUInfo('basic')) as {
          gpuDevice?: {
            vendorId?: number;
            deviceId?: number;
            active?: boolean;
            driverVersion?: string;
          }[];
        };
        const devices = info.gpuDevice ?? [];
        const active = devices.find((device) => device.active) ?? devices[0];
        return active
          ? `vendor 0x${(active.vendorId ?? 0).toString(16)} device 0x${(active.deviceId ?? 0).toString(16)}${active.driverVersion ? ` driver ${active.driverVersion}` : ''}`
          : null;
      },
    });
  }

  requestHeaders(): Record<string, string> {
    const session = this.activeSession();
    // Both proxies discard the renderer's Origin. Assert our own identity so
    // backend transport gates can recognize the desktop. Chromium forbids
    // overriding Origin itself in net.fetch.
    return {
      'X-VoiceStudio-Desktop-Origin': APP_ORIGIN,
      ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
    };
  }

  private activeSession(): RemoteSession | null {
    if (this.remoteSession && this.remoteSession.expiresAt > Date.now() / 1000) {
      return this.remoteSession;
    }
    this.remoteSession = null;
    return null;
  }

  get status(): BackendStatus {
    const status: BackendStatus = {
      stage: this.stage,
      baseUrl: this.baseUrl,
      port: this.port,
      managed: this.managed,
      remote: Boolean(this.remoteUrl),
      elapsedMs: Date.now() - this.startedAt,
      logTail: this.log.slice(-LOG_TAIL_LINES),
      lastCrash: this.crashes.latest(),
      ...(app.isPackaged
        ? {
            runtimePath: dirname(this.runtimeProject ?? join(defaultRuntimeRoot(), 'project')),
            runtimeCustom: !samePath(
              dirname(this.runtimeProject ?? join(defaultRuntimeRoot(), 'project')),
              defaultRuntimeRoot(),
            ),
            runtimeRegion: this.runtimeRegion,
            ...this.runtimeStorage(),
          }
        : {}),
    };
    if (this.stage === 'setup_required' && this.setupIssue) status.setupIssue = this.setupIssue;
    if (this.stage === 'setup_required' && this.setupIssue === 'space' && this.setupRequiredGib)
      status.setupRequiredGib = this.setupRequiredGib;
    if (this.stage === 'setup_required' && app.isPackaged && this.runtimeFreeGib !== undefined)
      status.runtimeFreeGib = this.runtimeFreeGib;
    if (this.stage === 'setup_required' && this.runtimeInterrupted)
      status.runtimeInterrupted = true;
    if (this.stage === 'setup_required' && this.runtimeRepairFailed)
      status.runtimeRepairFailed = true;
    if (this.stage === 'installing') {
      status.setupPhase = this.setupPhase;
      status.setupProgress = this.setupProgress.snapshot();
    }
    if (this.message !== undefined) status.message = this.message;
    if (this.diagnosis) status.diagnosis = this.diagnosis;
    if (this.exitCode !== undefined) status.exitCode = this.exitCode;
    if (this.exitSignal !== undefined) status.exitSignal = this.exitSignal;
    return status;
  }

  subscribe(listener: StatusListener): () => void {
    this.on('status', listener);
    return () => {
      this.off('status', listener);
    };
  }

  acknowledgeLastCrash(): NativeCrashRecord | undefined {
    const crash = this.crashes.acknowledgeLatest();
    this.emitStatus();
    return crash;
  }

  /** Attach to a backend already answering on the port, else spawn one. */
  async start(): Promise<void> {
    const gen = ++this.generation;
    this.shuttingDown = false;
    this.startedAt = Date.now();
    this.exitCode = undefined;
    this.exitSignal = undefined;
    // Every launch begins with an empty child-output ring. A restart must not
    // quote the backend it just killed, and a completed runtime install must
    // not quote the installer — setupRuntime's uv children share this ring.
    this.childLog.length = 0;
    this.childErrorLog.length = 0;
    this.lastChildOutputAt = 0;
    this.setStage('attaching', { managed: false, message: undefined });
    try {
      if (await this.probe()) {
        if (gen !== this.generation) return;
        this.runtimeInterrupted = false;
        await this.markReady(gen, {}, true);
        return;
      }
      if (gen !== this.generation) return;

      if (this.remoteUrl) {
        this.setStage('failed', {
          managed: false,
          message: `Could not reach the configured remote backend at ${this.remoteUrl}.`,
        });
        // Nothing else probes a `failed` remote. Without this, a Retry made
        // while the server is down leaves the status failed forever, even after
        // the server comes back.
        void this.recoverWhenRemoteReturns(gen);
        return;
      }

      if (process.env.VOICESTUDIO_SKIP_BACKEND === '1') {
        this.setStage('attaching', {
          message: `VOICESTUDIO_SKIP_BACKEND=1 — waiting for an external backend on port ${this.port}`,
        });
        void this.waitUntilReady(gen, Number.POSITIVE_INFINITY);
        return;
      }

      // A previous fallback is only useful while its backend still answers.
      // Once it is gone, retry the configured endpoint before spawning anew.
      if (this.localPort !== this.configuredPort) {
        this.localPort = this.configuredPort;
        const attached = await this.probe();
        if (gen !== this.generation) return;
        if (attached) {
          this.runtimeInterrupted = false;
          await this.markReady(gen, {}, true);
          return;
        }
      }

      if (app.isPackaged && !parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD)) {
        const { project, ready } = await this.resolveRuntimeProject();
        if (!ready) {
          if (gen === this.generation) {
            const action = decideMissingRuntime({
              packaged: true,
              previouslyInstalled: await runtimeWasInstalled(project),
              repairAttempted: this.repairAttempted,
              unsupported: isUnsupportedPlatform(),
              rocmNotYetInstalled: windowsRocmRequested() && !(await windowsRocmInstalled(project)),
            });
            this.runtimeRepairFailed = action === 'repair_failed';
            this.runtimeInterrupted = await runtimeInstallInterrupted(project);
            // Intel Macs can never resolve the runtime (#889), and the Intel
            // build under Rosetta resolves the same wheels (#2598): say so
            // now, before the setup screen offers an install that must fail.
            this.setupIssue = platformSetupIssue();
            this.setStage(
              'setup_required',
              action === 'repair_failed'
                ? {
                    message:
                      'The Python environment is missing and automatic repair did not finish. ' +
                      `See the engine log in ${app.getPath('logs')}.`,
                  }
                : undefined,
            );
            if (action === 'repair') {
              // Same flow as the setup wizard, one attempt per launch (no loop).
              this.repairAttempted = true;
              this.pushLog('out', `Python environment missing in ${project}; repairing it.`);
              void this.setupRuntime();
            }
          }
          return;
        }
        this.runtimeInterrupted = false;
        await stageRuntimeSources(backendRoot(), project);
        if (gen !== this.generation) return;
      }
      // Explicit ports/custom commands are contracts with external callers. Only
      // the default managed launch may move away from an OS-reserved port.
      if (
        !process.env.OMNIVOICE_PORT?.trim() &&
        !parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD)
      ) {
        let identifiedBackend = false;
        const port = await availableBackendPort(this.port, async (candidate) => {
          identifiedBackend = await this.probe(`http://127.0.0.1:${candidate}`, true);
          return identifiedBackend;
        });
        if (gen !== this.generation) return;
        this.localPort = port;
        if (port !== this.configuredPort) {
          const attached = await this.probe();
          if (gen !== this.generation) return;
          if (attached) {
            await this.markReady(gen, {}, true);
            return;
          }
          if (identifiedBackend) {
            // Another instance owns this listener but is still loading. Never
            // spawn over it; attachment has the same bounded startup budget.
            void this.waitUntilReady(gen, startupBudgetMs());
            return;
          }
        }
      }
      const plan = await resolveSpawnPlan(this.port, this.runtimeProject ?? undefined);
      if (gen !== this.generation) return;
      if ('error' in plan) {
        if (plan.setupIssue) {
          // Same screen an installed app shows on this host: remote-backend
          // guidance instead of a Retry that can never succeed.
          this.setupIssue = plan.setupIssue;
          this.pushLog('err', plan.error);
          this.setStage('setup_required', { message: undefined });
          return;
        }
        this.setStage('failed', { message: plan.error });
        return;
      }
      this.spawnChild(plan, gen);
      if (gen !== this.generation || this.stage !== 'starting') return;
      void this.waitUntilReady(gen, startupBudgetMs());
    } catch (error) {
      if (gen !== this.generation) return;
      const message = runtimeFailureMessage(error);
      this.pushLog('err', message);
      this.setStage('failed', { message });
    }
  }

  /** Explicit first-run action. Installation never happens in start()/restart(). */
  async setupRuntime(): Promise<void> {
    if (
      !runtimeSetupSupported() ||
      !app.isPackaged ||
      this.installation ||
      this.cleaningRuntime ||
      this.stage !== 'setup_required' ||
      isUnsupportedPlatform()
    )
      return;
    let project =
      this.runtimeProject ?? join(storedRuntimeRoot() ?? defaultRuntimeRoot(), 'project');
    this.runtimeProject = project;
    const controller = new AbortController();
    this.installation = controller;
    const gen = ++this.generation;
    this.startedAt = Date.now();
    this.log.length = 0;
    this.childLog.length = 0;
    this.childErrorLog.length = 0;
    this.setupIssue = undefined;
    this.setupRequiredGib = undefined;
    this.runtimeRepairFailed = false;
    this.runtimeInterrupted = false;
    this.setupPhase = 'checking';
    this.setupProgress.reset();
    this.setStage('installing', { message: undefined });
    const stopWatching = this.watchSetupDownloads(gen, controller.signal);
    try {
      const reusable = await runtimeProjectReady(backendRoot(), project);
      if (gen !== this.generation || controller.signal.aborted) return;
      if (reusable) {
        await this.start();
        return;
      }
      let runtimeRoot = dirname(project);
      const configured = storedRuntimeLocation();
      if (
        configured &&
        !configured.owned &&
        samePath(configured.root, runtimeRoot) &&
        !samePath(runtimeRoot, defaultRuntimeRoot()) &&
        holdsUnownedFiles(runtimeRoot)
      ) {
        // An explicit setup action may create a new runtime, but must never
        // take ownership of (or repair in place) another installation's files.
        runtimeRoot = defaultRuntimeRoot();
        project = join(runtimeRoot, 'project');
        this.runtimeProject = project;
        writeRuntimeLocation(runtimeRoot, true);
        this.pushLog(
          'out',
          'Creating a separate Electron runtime; existing environment preserved.',
        );
        this.emitStatus();
        const fallbackReusable = await runtimeProjectReady(backendRoot(), project);
        if (gen !== this.generation || controller.signal.aborted) return;
        if (fallbackReusable) {
          await this.start();
          return;
        }
      }
      if (
        configured &&
        samePath(configured.root, runtimeRoot) &&
        !samePath(runtimeRoot, defaultRuntimeRoot())
      ) {
        // Electron may create or replace runtime files from this point forward.
        // Keep reused Tauri environments unowned so uninstall never removes them.
        writeRuntimeLocation(runtimeRoot, true);
      }
      await installRuntime(
        backendRoot(),
        project,
        findUv(),
        (command, args, cwd, env, options) => {
          // A planning run (uv's dry run) is read, not shown: its package list
          // would read as installs to the progress tracker.
          if (options?.quiet)
            return runRuntimeProbe(
              command,
              args,
              cwd,
              { ...childEnv(this.port, this.runtimeRegion), ...env },
              {
                timeoutMs: options.timeoutMs,
                signal: controller.signal,
                captureStderr: true,
              },
            );
          if (options?.timeoutMs)
            return runRuntimeProbe(
              command,
              args,
              cwd,
              { ...childEnv(this.port, this.runtimeRegion), ...env },
              {
                timeoutMs: options.timeoutMs,
                signal: controller.signal,
                onSpawn: (child) => {
                  this.child = child;
                  this.attachLineReader(child.stdout, 'out');
                  this.attachLineReader(child.stderr, 'err');
                },
                onClose: (child) => {
                  if (this.child === child) this.child = null;
                },
              },
            );
          return new Promise<string>((resolve, reject) => {
            if (controller.signal.aborted) {
              reject(controller.signal.reason);
              return;
            }
            const launchFailed = (err: unknown) =>
              reject(
                Object.assign(new Error(spawnFailureMessage(command, err)), {
                  code: (err as NodeJS.ErrnoException | undefined)?.code,
                }),
              );
            let child: ChildProcess;
            try {
              child = spawn(command, args, {
                cwd,
                env: { ...childEnv(this.port, this.runtimeRegion), ...env },
                windowsHide: true,
                detached: process.platform !== 'win32',
                stdio: ['pipe', 'pipe', 'pipe'],
              });
            } catch (err) {
              // Windows throws a refused launch here instead of emitting `error`.
              launchFailed(err);
              return;
            }
            this.child = child;
            let capturedStdout = '';
            child.stdin?.on('error', () => {});
            child.stdout?.on('data', (chunk: Buffer | string) => {
              capturedStdout = (capturedStdout + chunk.toString()).slice(-65_536);
            });
            this.attachLineReader(child.stdout, 'out');
            this.attachLineReader(child.stderr, 'err');
            child.on('error', launchFailed);
            child.on('close', (code) => {
              if (this.child === child) this.child = null;
              if (code === 0) resolve(capturedStdout);
              else
                reject(
                  Object.assign(new Error(`Runtime setup exited with code ${code}`), {
                    code,
                    stdout: capturedStdout,
                  }),
                );
            });
          });
        },
        controller.signal,
        (phase) => {
          if (gen !== this.generation) return;
          this.setupPhase = phase;
          this.emitStatus();
        },
        this.runtimeRegion,
        {
          log: (line) => {
            if (gen === this.generation) this.pushLog('out', line);
          },
          transfer: (name, received, total) => {
            if (gen !== this.generation) return;
            this.setupProgress.observeTransfer(name, received, total);
            this.emitStatus();
          },
          pypiHost: (host) => {
            if (gen !== this.generation) return;
            void runtimeDownloadHosts(backendRoot(), process.platform, process.arch, host)
              .then((hosts) => this.setupProgress.setDownloadHosts(hosts))
              .catch(() => undefined);
          },
        },
      );
      if (gen === this.generation) await this.start();
    } catch (error) {
      if (gen === this.generation) {
        const code = (error as NodeJS.ErrnoException)?.code;
        this.setupIssue =
          code === 'INTEL_MAC_UNSUPPORTED'
            ? (platformSetupIssue() ?? 'unsupported_platform')
            : code === 'ENOSPC'
              ? 'space'
              : code === MISSING_SYSTEM_LIBRARY_CODE
                ? 'missing_system_library'
                : code === OUTDATED_SYSTEM_LIBRARY_CODE
                  ? 'outdated_system_library'
                  : code === RUNTIME_FILES_LOCKED_CODE
                    ? 'files_locked'
                    : ['EACCES', 'EPERM', 'EROFS'].includes(code || '')
                      ? 'access'
                      : undefined;
        this.setupRequiredGib =
          code === 'ENOSPC' ? (error as { requiredGib?: number }).requiredGib : undefined;
        this.runtimeRepairFailed = this.repairAttempted;
        this.runtimeInterrupted = await runtimeInstallInterrupted(project);
        const message = runtimeFailureMessage(error);
        this.pushLog('err', message);
        this.setStage('setup_required', { message });
      }
    } finally {
      stopWatching();
      if (this.installation === controller) this.installation = null;
    }
  }

  /**
   * uv prints nothing while a large wheel downloads, so watch its cache for
   * in-flight bytes: the setup screen names the source host, and a download
   * that stops receiving data shows as stalled instead of frozen (#3411).
   */
  private watchSetupDownloads(gen: number, signal: AbortSignal): () => void {
    const reporter = new SetupDownloadReporter();
    void Promise.resolve()
      .then(() =>
        runtimeDownloadHosts(
          backendRoot(),
          process.platform,
          process.arch,
          pypiFilesHost(this.runtimeRegion),
        ),
      )
      .then((hosts) => this.setupProgress.setDownloadHosts(hosts))
      .catch(() => undefined);
    let sampling = false;
    const timer = setInterval(() => {
      if (sampling || signal.aborted || gen !== this.generation || this.stage !== 'installing')
        return;
      if (this.setupPhase !== 'installing_deps' || !this.runtimeProject) return;
      sampling = true;
      void inFlightDownloadBytes(runtimeUvCache(this.runtimeProject))
        .then((bytes) => {
          if (signal.aborted || gen !== this.generation || this.stage !== 'installing') return;
          this.setupProgress.observeInFlight(bytes);
          const snapshot = this.setupProgress.snapshot();
          for (const line of reporter.lines(snapshot)) this.pushLog('out', line);
          if (snapshot.downloadsComplete === false) this.emitStatus();
        })
        .catch(() => undefined)
        .finally(() => {
          sampling = false;
        });
    }, 3_000);
    timer.unref?.();
    return () => clearInterval(timer);
  }

  /** Remove only an Electron-owned Python project, then perform a fresh install. */
  async cleanSetupRuntime(): Promise<void> {
    if (
      !runtimeSetupSupported() ||
      !app.isPackaged ||
      this.installation ||
      this.cleaningRuntime ||
      this.stage !== 'setup_required'
    )
      return;
    const configured = storedRuntimeLocation();
    const candidate =
      this.runtimeProject ?? join(configured?.root ?? defaultRuntimeRoot(), 'project');
    const runtimeRoot = dirname(candidate);
    const project = join(runtimeRoot, 'project');
    if (!samePath(candidate, project))
      throw new Error('Runtime cleanup refused for an invalid project path');
    const owned =
      samePath(runtimeRoot, defaultRuntimeRoot()) ||
      Boolean(configured?.owned && samePath(configured.root, runtimeRoot));
    if (!owned) throw new Error('Runtime cleanup refused for an unowned environment');
    this.runtimeProject = project;
    const gen = this.generation;
    this.cleaningRuntime = true;
    try {
      await promoteLegacyRuntimeCaches(project);
      await rm(project, { recursive: true, force: true });
    } catch (error) {
      if (gen === this.generation) {
        this.setupIssue = ['EACCES', 'EPERM', 'EROFS'].includes(
          (error as NodeJS.ErrnoException).code || '',
        )
          ? 'access'
          : undefined;
        const message = runtimeFailureMessage(error);
        this.pushLog('err', message);
        this.setStage('setup_required', { message });
      }
      return;
    } finally {
      this.cleaningRuntime = false;
    }
    if (gen !== this.generation) return;
    this.setupIssue = undefined;
    this.setupRequiredGib = undefined;
    this.runtimeInterrupted = false;
    this.setupPhase = 'checking';
    this.message = undefined;
    await this.setupRuntime();
  }

  /** Kill the managed child (if any) and run the attach-or-spawn sequence again. */
  async restart(): Promise<void> {
    this.installation?.abort();
    this.generation++;
    await this.killChild(true);
    await this.start();
  }

  /** Tear the backend down: process tree first, then the stdin liveness pipe. */
  async shutdown(): Promise<void> {
    this.generation++;
    this.shuttingDown = true;
    this.installation?.abort();
    await this.killChild(true);
    this.exitCode = undefined;
    this.exitSignal = undefined;
    this.setStage('idle', { managed: false, message: undefined });
  }

  async testRemote(url: string, apiKey: string): Promise<RemoteBackendProbe> {
    const result = await probeRemoteBackend(url, apiKey);
    if (result.ok) this.testedRemote = { url: result.target, session: result.session };
    else this.testedRemote = null;
    return result.ok
      ? {
          ok: true,
          detail: result.detail,
          target: result.target,
          authenticated: Boolean(result.session),
        }
      : result;
  }

  async useRemote(url: string, apiKey: string): Promise<RemoteBackendProbe> {
    const trimmed = url.trim().replace(/\/+$/, '');
    let result;
    if (!apiKey.trim() && this.testedRemote?.url === trimmed) {
      result = {
        ok: true as const,
        detail: '',
        target: this.testedRemote.url,
        session: this.testedRemote.session,
      };
    } else {
      result = await probeRemoteBackend(url, apiKey);
    }
    if (!result.ok) return result;
    saveRemoteBackend(this.remotePath, result.target);
    this.remoteUrl = result.target;
    this.remoteSession = result.session;
    this.testedRemote = null;
    await this.restart();
    return {
      ok: true,
      detail: result.detail,
      target: result.target,
      authenticated: Boolean(result.session),
    };
  }

  async useLocal(): Promise<BackendConnection> {
    const target = this.remoteUrl;
    const session = this.activeSession();
    this.remoteUrl = null;
    this.remoteSession = null;
    this.testedRemote = null;
    saveRemoteBackend(this.remotePath, null);
    if (target && session) {
      void fetch(`${target}/api/auth/session`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${session.token}` },
        signal: AbortSignal.timeout(1500),
      }).catch(() => {});
    }
    await this.restart();
    return this.connection;
  }

  private runtimeStorage(): { runtimeStorage?: ScopedStorage } {
    const scoped = scopedRuntimeRoot();
    const root = this.runtimeProject ? dirname(this.runtimeProject) : null;
    return scoped && root && samePath(scoped, root)
      ? { runtimeStorage: scopedStorage(scoped) }
      : {};
  }

  setRuntimeLocation(parent: string | null): { path: string; custom: boolean } {
    if (
      !app.isPackaged ||
      // A failed start can be fixed by moving the runtime (permission, synced
      // folder, missing environment), so those stages may change location too.
      !['setup_required', 'failed', 'crashed', 'port_in_use'].includes(this.stage) ||
      this.installation ||
      this.cleaningRuntime
    )
      throw new Error('runtime_location_unavailable');
    const defaultRoot = defaultRuntimeRoot();
    const root = parent === null ? defaultRoot : selectedRuntimeRoot(parent);
    const locationFile = join(app.getPath('userData'), RUNTIME_LOCATION_FILE);
    if (samePath(root, defaultRoot)) {
      rmSync(locationFile, { force: true });
    } else {
      writeRuntimeLocation(root, false, true);
    }
    this.runtimeProject = join(root, 'project');
    this.message = undefined;
    this.emitStatus();
    return { path: root, custom: !samePath(root, defaultRoot) };
  }

  setRuntimeRegion(raw: unknown): RuntimeRegion {
    if (this.stage !== 'setup_required' || this.installation || this.cleaningRuntime)
      throw new Error('runtime_region_unavailable');
    if (typeof raw !== 'string' || !RUNTIME_REGIONS.has(raw as RuntimeRegion))
      throw new Error('invalid_runtime_region');
    this.runtimeRegion = raw as RuntimeRegion;
    writeFileSync(
      runtimePreferencesPath(),
      JSON.stringify({ region: this.runtimeRegion }, null, 2),
    );
    this.emitStatus();
    return this.runtimeRegion;
  }

  /** A custom runtime is removable only when this Electron install created it. */
  get ownedRuntimeRoot(): string | null {
    const configured = storedRuntimeLocation();
    return configured?.owned === true && !samePath(configured.root, defaultRuntimeRoot())
      ? configured.root
      : null;
  }

  /**
   * What an uninstall may remove from the runtime location. A location chosen in
   * setup also holds the user's data and models, which the uninstall prompt
   * handles as separate choices, so only the runtime's own folders are listed.
   */
  get ownedRuntimeEntries(): string | string[] | null {
    const root = this.ownedRuntimeRoot;
    if (!root || storedRuntimeLocation()?.scoped !== true) return root;
    return ['project', '.uv-cache', '.python', '.tmp', 'tmp', '.wheel-prefetch'].map((name) =>
      join(root, name),
    );
  }

  websocketUrl(path: '/ws/transcribe' | '/ws/events' | '/ws/tts'): Promise<string> {
    return remoteWebSocketUrl(this.baseUrl, path, this.remoteUrl ? this.activeSession() : null);
  }

  private setStage(
    stage: BackendStage,
    patch: {
      managed?: boolean;
      message?: string | undefined;
      diagnosis?: BackendStatus['diagnosis'];
    } = {},
  ): void {
    this.stage = stage;
    this.diagnosis = patch.diagnosis;
    if ('managed' in patch) this.managed = patch.managed ?? false;
    if ('message' in patch) this.message = patch.message;
    this.emitStatus();
  }

  private async resolveRuntimeProject(): Promise<{ project: string; ready: boolean }> {
    const bundle = backendRoot();
    const own = join(defaultRuntimeRoot(), 'project');
    const configuredRoot = storedRuntimeRoot();
    const configured = configuredRoot ? join(configuredRoot, 'project') : null;
    // Explicit selection is authoritative, including when it needs setup.
    const candidates = (
      configured ? [configured] : [this.runtimeProject, own, ...legacyTauriRuntimeProjects()]
    ).filter((candidate): candidate is string => Boolean(candidate));
    const selected = configured ?? own;
    let selectedRefusal: RuntimeLaunchRefusedError | undefined;
    for (const project of new Set(candidates.map((candidate) => resolve(candidate)))) {
      try {
        if (await runtimeProjectReady(bundle, project)) {
          this.runtimeProject = project;
          if (project !== own && project !== configured)
            this.pushLog('out', `Reusing compatible Tauri runtime: ${project}`);
          return { project, ready: true };
        }
      } catch (error) {
        if (!(error instanceof RuntimeLaunchRefusedError)) throw error;
        // A legacy candidate must not prevent setup or reuse of another one.
        if (samePath(project, selected)) selectedRefusal = error;
      }
    }
    this.runtimeProject = selected;
    if (selectedRefusal) throw selectedRefusal;
    return { project: this.runtimeProject, ready: false };
  }

  private emitStatus(): void {
    this.probeRuntimeFree();
    this.emit('status', this.status);
  }

  /** Reads the free space of the install drive once per location so the setup screen can show it. */
  private probeRuntimeFree(): void {
    if (this.stage !== 'setup_required' || !app.isPackaged) {
      this.freeProbeRoot = undefined;
      return;
    }
    const root = dirname(this.runtimeProject ?? join(defaultRuntimeRoot(), 'project'));
    if (this.freeProbeRoot === root) return;
    this.freeProbeRoot = root;
    void freeGb(root)
      .then((gb) => {
        if (this.freeProbeRoot !== root) return;
        this.runtimeFreeGib = gb ?? undefined;
        this.emit('status', this.status);
      })
      .catch(() => undefined);
  }

  private pushLog(stream: 'out' | 'err', line: string, fromChild = false): void {
    // Backend output can carry a token or a home directory (and with it the
    // user's name). It is quoted in failure messages, shown in the log tail and
    // forwarded to repair agents, so it is scrubbed once, here, at the source.
    line = scrubText(cleanProcessLine(line));
    if (!line) return;
    if (stream === 'err') this.crashes.captureLine(line);
    this.log.push(line);
    if (this.log.length > LOG_RING_LINES) this.log.splice(0, this.log.length - LOG_RING_LINES);
    // Failure messages quote the backend, not this shell. `log` also carries
    // the supervisor's own "Reusing compatible Tauri runtime"/"spawning in …"
    // lines, so taking its tail would report a launch banner as the backend's
    // last word — exactly the evidence a startup failure needs.
    if (fromChild) {
      this.lastChildOutputAt = Date.now();
      this.childLog.push(line);
      if (this.childLog.length > LOG_RING_LINES)
        this.childLog.splice(0, this.childLog.length - LOG_RING_LINES);
      if (stream === 'err') {
        this.childErrorLog.push(line);
        if (this.childErrorLog.length > LOG_RING_LINES)
          this.childErrorLog.splice(0, this.childErrorLog.length - LOG_RING_LINES);
      }
    }
    (stream === 'err' ? console.error : console.log)(`[backend] ${line}`);
    if (this.stage === 'installing') {
      this.setupProgress.ingest(line);
      this.emitStatus();
    }
  }

  private attachLineReader(readable: NodeJS.ReadableStream | null, stream: 'out' | 'err'): void {
    if (!readable) return;
    let pending = '';
    const flushPending = () => {
      if (pending.length > 0) this.pushLog(stream, pending, true);
      pending = '';
    };
    readable.setEncoding('utf8');
    readable.on('data', (chunk: string) => {
      pending += chunk;
      const lines = pending.split(/[\r\n]+/);
      pending = lines.pop() ?? '';
      for (const line of lines) if (line.length > 0) this.pushLog(stream, line, true);
    });
    readable.on('end', flushPending);
    readable.on('error', (error: unknown) => {
      flushPending();
      if (!isExpectedPipeClose(error)) {
        this.pushLog('err', `Backend ${stream} stream failed: ${errorMessage(error)}`);
      }
    });
  }

  /** Launch the resolved backend command and wire its lifecycle events. */
  private spawnChild(plan: SpawnPlan, gen: number): void {
    this.crashes.resetCapture();
    const [command, ...args] = plan.argv;
    // A custom command bypasses the managed runtime; its own executable is the
    // only thing that can be repaired.
    const runtimeOwned = !parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD);
    if (!command) {
      this.setStage('failed', { message: 'Empty backend command' });
      return;
    }
    console.log(`[backend] spawning in ${plan.cwd}: ${plan.argv.join(' ')}`);
    let child: ChildProcess;
    const processOptions = managedBackendSpawnOptions(this.port, this.runtimeRegion);
    try {
      child = spawn(command, args, {
        cwd: plan.cwd,
        env: processOptions.env,
        // stdin is the liveness contract: it stays open, unwritten, until quit.
        stdio: processOptions.stdio,
        windowsHide: true,
        // POSIX: own process group so the whole tree can be signalled at once.
        detached: process.platform !== 'win32',
      });
    } catch (err) {
      this.setStage('failed', {
        message: spawnFailureMessage(command, err, { runtimeOwned }),
      });
      return;
    }
    this.child = child;
    if (processOptions.drainFd !== null) {
      // Python passes this child-side descriptor to every nested operation.
      // Reading the parent side keeps the ownership channel live and lets
      // Node observe EOF only after the complete backend subtree releases it.
      const drain = child.stdio?.[processOptions.drainFd] as
        | NodeJS.ReadableStream
        | null
        | undefined;
      drain?.on('error', (error: unknown) => {
        if (!isExpectedPipeClose(error)) {
          this.pushLog('err', `Backend drain stream failed: ${errorMessage(error)}`);
        }
      });
      drain?.resume();
    }
    // EPIPE on a dying child must never take main down with it.
    child.stdin?.on('error', () => {});
    this.attachLineReader(child.stdout, 'out');
    this.attachLineReader(child.stderr, 'err');
    child.on('error', (err) => {
      if (gen !== this.generation) return;
      this.child = null;
      this.setStage('failed', {
        message: spawnFailureMessage(command, err, { runtimeOwned }),
      });
    });
    child.on('exit', (code, signal) => {
      if (this.child === child) this.child = null;
      if (gen !== this.generation || this.shuttingDown) return;
      void this.recoverAfterChildExit(gen, code, signal);
    });
    this.setStage('starting', { managed: true, message: undefined });
  }

  private async recoverAfterChildExit(
    gen: number,
    code: number | null,
    signal: NodeJS.Signals | null,
  ): Promise<void> {
    // A second VoiceStudio shell can deliberately replace this child between
    // process exit and its own listener becoming ready. Treat that handoff as
    // attachment, not a crash, and keep renderer requests paused meanwhile.
    this.setStage('attaching', { managed: false, message: undefined });
    const deadline = Date.now() + REPLACEMENT_ATTACH_GRACE_MS;
    while (gen === this.generation && !this.shuttingDown) {
      if (await this.probe()) {
        if (gen !== this.generation || this.shuttingDown) return;
        this.exitCode = undefined;
        this.exitSignal = undefined;
        this.pushLog('out', 'Attached to the replacement VoiceStudio backend.');
        await this.markReady(gen, { managed: false, message: undefined }, true);
        return;
      }
      if (Date.now() >= deadline) break;
      await delay(READY_POLL_MS);
    }
    if (gen !== this.generation || this.shuttingDown) return;

    // End every readiness/supervisor loop for the dead ownership generation
    // before publishing the terminal result.
    this.generation++;
    const recorded = this.crashes.record(code, signal, Date.now() - this.startedAt, this.log);
    this.exitCode = code;
    this.exitSignal = signal;
    const bindFailure =
      code === EXIT_PORT_IN_USE ? 'in_use' : bindFailureFromLog(this.childLog.join('\n'));
    if (bindFailure === 'in_use') {
      this.setStage('port_in_use', {
        message: `Port ${this.port} is already in use by another process. Stop it or set OMNIVOICE_PORT.`,
      });
      return;
    }
    if (bindFailure === 'denied') {
      this.setStage('port_in_use', {
        message:
          `Windows or another program refused access to port ${this.port}. It is often in a reserved ` +
          'port range (Hyper-V, WSL, Docker) - run "netsh interface ipv4 show excludedportrange protocol=tcp" ' +
          'to check - or held exclusively. Set OMNIVOICE_PORT to a free port and restart.',
      });
      return;
    }
    // A native fault's last line is whatever followed the dump: the stdlib
    // frame that started the process, an extension-module list or an access
    // log line. Name the fault and where it happened instead (#2382, #2187).
    const lastLine = nativeFaultSummary(recorded?.logTail.join('\n') ?? '') || this.childLog.at(-1);
    const why = signal ? `signal ${signal}` : `exit code ${code}`;
    // The backend's own verdict names the cause of a failed start. Without it
    // the message quoted uvicorn's access log for the last /health poll —
    // "503 Service Unavailable" — and hid the import error that actually
    // killed the process (#3058).
    const startup = backendStartupFailure(this.childErrorLog);
    // Windows refused to load one of the runtime's files: name who did, since
    // the quoted DLL error alone reads like a broken install (#3209).
    // Python words the refusal in the Windows display language, so a non-English
    // message is matched by the Smart App Control state instead.
    const loadBlock =
      windowsLoadBlock(this.childErrorLog) ??
      (process.platform === 'win32' &&
      /dll load failed/i.test(this.childErrorLog.slice(-20).join('\n')) &&
      // A missing DLL is a broken runtime that repair can fix, not a refusal.
      !/the specified module could not be found|could not find module/i.test(
        this.childErrorLog.slice(-20).join('\n'),
      ) &&
      ['on', 'evaluation'].includes(readSmartAppControl())
        ? 'application_control'
        : null);
    if (loadBlock) {
      this.setStage('crashed', {
        managed: true,
        message: loadBlockMessage(why, lastLine ?? '', loadBlock),
      });
      return;
    }
    // A backend that died while importing its own dependencies is a broken
    // environment, not a crash to retry: its wheels are damaged or a system
    // library they link against is gone. Repair the managed runtime once per
    // launch instead of leaving the user with a dead app (#3058).
    if (this.canRepairRuntime() && isBrokenEnvironmentOutput(this.childErrorLog)) {
      this.repairAttempted = true;
      this.pushLog(
        'out',
        'The backend died while loading its Python dependencies; repairing the runtime.',
      );
      this.setStage('setup_required', { managed: true, message: undefined });
      void this.setupRuntime();
      return;
    }
    this.setStage('crashed', {
      // The child was ours: the failure belongs to the managed runtime, and a
      // report that says "Managed: false" sends the triage the wrong way.
      managed: true,
      message: startup
        ? `Backend failed to start during '${startup.step}' (${why}): ${startup.reason}`
        : `Backend exited unexpectedly (${why}).${lastLine ? ` Last output: ${lastLine}` : ''}`,
    });
  }

  /**
   * Whether this launch may rebuild the managed runtime on its own.
   *
   * The same guard the setup flow uses: a packaged app with a user-initiated
   * runtime, no custom backend command, a resolved runtime project and no
   * install already running.
   */
  /**
   * Whether the resolved runtime project is one this install created. A
   * compatible legacy Tauri project can be adopted as the runtime; a crash-time
   * repair must never run `uv sync` inside files another install owns.
   */
  private ownsRuntimeProject(): boolean {
    const project = this.runtimeProject;
    if (!project) return false;
    if (samePath(project, join(defaultRuntimeRoot(), 'project'))) return true;
    const configured = storedRuntimeLocation();
    return Boolean(configured?.owned && samePath(project, join(configured.root, 'project')));
  }

  private canRepairRuntime(): boolean {
    return (
      app.isPackaged &&
      runtimeSetupSupported() &&
      !parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD) &&
      !isUnsupportedPlatform() &&
      this.ownsRuntimeProject() &&
      !this.repairAttempted &&
      !this.installation &&
      !this.cleaningRuntime
    );
  }

  private async probe(baseUrl = this.baseUrl, identityOnly = false): Promise<boolean> {
    this.lastProbeOutcome = 'ok';
    try {
      // This runs for the entire desktop session. Use the canonical, tiny
      // liveness response instead of repeatedly serializing full hardware,
      // settings and path information from /system/info.
      const res = await fetch(`${baseUrl}/health`, {
        headers: this.requestHeaders(),
        signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
        redirect: this.remoteUrl ? 'follow' : 'error',
      });
      // Every local backend this shell can spawn or attach to stamps the
      // marker on every response (BackendMarkerMiddleware, #1385). A generic
      // health JSON on a loopback port, configured or fallback, must never
      // redirect renderer content to another service; an unmarked listener
      // is left alone and the launch reports the port as in use instead.
      // Remote backends were chosen explicitly and may predate the marker.
      const marked = Boolean(res.headers.get('x-omnivoice-backend'));
      if (identityOnly) return marked;
      if (!this.remoteUrl && !marked) {
        this.lastProbeOutcome = 'refused';
        return false;
      }
      if (!res.ok) {
        this.lastProbeOutcome = 'rejected';
        return false;
      }
      const body: unknown = await res.json();
      const healthy =
        typeof body === 'object' &&
        body !== null &&
        (body as { status?: unknown }).status === 'ok' &&
        typeof (body as { version?: unknown }).version === 'string';
      if (!healthy) this.lastProbeOutcome = 'rejected';
      return healthy;
    } catch (error) {
      const name = (error as { name?: unknown } | null)?.name;
      this.lastProbeOutcome =
        name === 'TimeoutError' || name === 'AbortError' ? 'timeout' : 'refused';
      return false;
    }
  }

  /** Keep probing a configured remote that was unreachable; resume supervision when it answers. */
  private async recoverWhenRemoteReturns(gen: number): Promise<void> {
    while (gen === this.generation && this.stage === 'failed' && this.remoteUrl) {
      await delay(SUPERVISE_POLL_MS);
      if (gen !== this.generation || this.stage !== 'failed') return;
      if (await this.probe()) {
        if (gen !== this.generation || this.stage !== 'failed') return;
        await this.markReady(gen, { message: undefined }, true);
        return;
      }
    }
  }

  /**
   * Whether a remote accepts this client's credentials. /health needs no admin
   * session, so it can answer while every authenticated call fails (the session
   * expired during an outage, or the key was rotated). Same check the remote
   * probe applies when connecting: an authenticated /system/info. Three-way,
   * because a timeout or a 5xx proves nothing about the session: only a 2xx is
   * `valid` and only 401/403 is `rejected`.
   */
  private async remoteCredentials(): Promise<'valid' | 'rejected' | 'inconclusive'> {
    if (!this.remoteUrl) return 'valid';
    try {
      const res = await fetch(`${this.baseUrl}/system/info`, {
        headers: this.requestHeaders(),
        signal: AbortSignal.timeout(REMOTE_AUTH_CHECK_TIMEOUT_MS),
        redirect: 'follow',
      });
      if (res.ok) return 'valid';
      return res.status === 401 || res.status === 403 ? 'rejected' : 'inconclusive';
    } catch {
      return 'inconclusive';
    }
  }

  /**
   * The one place a backend that just answered /health becomes `ready`. A local
   * backend is trusted on /health alone; a remote must also prove its
   * credentials are accepted, otherwise the workspace would resume while every
   * authenticated API and WebSocket call fails.
   *
   *  - valid: `ready`.
   *  - rejected: `failed` with the auth diagnosis.
   *  - inconclusive: the CURRENT state is kept. A `failed`/`unresponsive` remote
   *    is never promoted on an unverified session, and a first start reports
   *    uncertain connectivity (`unresponsive`) instead of `ready`. The
   *    supervisor re-checks on its next tick.
   *
   * Returns whether the stage is now `ready`.
   */
  private async markReady(
    gen: number,
    patch: { managed?: boolean; message?: string | undefined },
    superviseAfter: boolean,
  ): Promise<boolean> {
    const credentials = await this.remoteCredentials();
    if (gen !== this.generation) return false;
    if (credentials === 'valid') {
      // A healthy run must not leave import errors behind: a later unrelated
      // exit would otherwise match them and be misread as a broken runtime.
      this.childErrorLog.length = 0;
      this.setStage('ready', patch);
    } else if (credentials === 'rejected') {
      if (this.diagnosis !== 'auth_required' || this.stage !== 'failed') {
        this.setStage('failed', {
          managed: false,
          message: `The remote backend at ${this.baseUrl} no longer accepts this app's credentials (its admin session expired or the key changed). Reconnect it with the API key.`,
          diagnosis: 'auth_required',
        });
      }
    } else if (this.stage !== 'failed' && this.stage !== 'unresponsive') {
      this.setStage('unresponsive', {
        managed: false,
        message: `Cannot confirm that the remote backend at ${this.baseUrl} accepts this app's credentials: its authenticated check did not complete. Retrying automatically.`,
        diagnosis: 'remote_unreachable',
      });
    }
    if (superviseAfter) this.supervise(gen);
    return credentials === 'valid';
  }

  /** Poll /health until ready, retiring the launch when the budget expires. */
  private async waitUntilReady(gen: number, budgetMs: number): Promise<void> {
    // `OMNIVOICE_STARTUP_BUDGET_S` bounds how long the *backend* may take to
    // answer, so it is measured from the moment this poll loop begins — which
    // is right after the process was spawned. Anchoring it to `startedAt`
    // charged the launch against the backend's window (#2445), and everything
    // ahead of the spawn is slow and independently bounded: resolving the
    // runtime imports torch in a child interpreter (30 s each, once per
    // candidate project, then again in resolveSpawnPlan), staging the bundled
    // sources is a recursive copy, and port selection walks up to 17
    // candidates. Once that pre-spawn work outlasted the budget, this loop
    // failed on its very first probe — killing a backend that had been alive
    // for a second and reporting "did not answer within 300 s".
    const pollStartedAt = Date.now();
    const waitingStage = this.stage;
    while (gen === this.generation && this.stage === waitingStage) {
      const ready = await this.probe();
      // Child-exit recovery owns its own grace period. Its transition from
      // starting to attaching must retire this launch's readiness deadline.
      if (gen !== this.generation || this.stage !== waitingStage) return;
      if (ready) {
        await this.markReady(gen, { message: undefined }, true);
        return;
      }
      if (gen !== this.generation || (this.stage !== 'starting' && this.stage !== 'attaching')) {
        return;
      }
      if (Date.now() > readinessDeadline(pollStartedAt, budgetMs, this.lastChildOutputAt)) {
        this.generation++;
        // killChild() nulls this.child, so record whether this launch owned a
        // process *before* tearing it down. Checking afterwards would
        // suppress the one diagnostic that matters — a managed backend that
        // died silently — and would let an attach-only wait blame output from
        // a backend this attempt never started.
        const owned = this.child !== null;
        // Taken before teardown too: killing the child can append shutdown
        // output, which must not replace the startup line the report needs.
        const lastOutput = owned ? this.childLog.at(-1) : undefined;
        await this.killChild();
        this.setStage('failed', {
          message: startupTimeoutMessage(this.port, budgetMs, { owned, lastOutput }),
        });
        return;
      }
      await delay(READY_POLL_MS);
    }
  }

  private supervise(gen: number): void {
    if (this.supervisingGeneration === gen) return;
    this.supervisingGeneration = gen;
    let misses = 0;
    // Consecutive refused connections, classified from the LATEST probes: a
    // refusal followed by a timeout or an answer means the listener is back.
    let refusals = 0;
    // Consecutive answered-but-unhealthy probes, counted apart from refusals.
    let rejections = 0;
    // Healthy ticks since an auth_required remote was last re-validated.
    let authRecheck = 0;
    const noteMiss = (): number => {
      refusals = this.lastProbeOutcome === 'refused' ? refusals + 1 : 0;
      rejections = this.lastProbeOutcome === 'rejected' ? rejections + 1 : 0;
      return ++misses;
    };
    const release = (): void => {
      if (this.supervisingGeneration === gen) this.supervisingGeneration = null;
    };
    const tick = async (): Promise<void> => {
      await delay(SUPERVISE_POLL_MS);
      if (gen !== this.generation) {
        release();
        return;
      }
      if (await this.probe()) {
        misses = 0;
        refusals = 0;
        rejections = 0;
        if (gen === this.generation && RECOVERABLE_STAGES.has(this.stage)) {
          // A remote parked on auth_required is re-validated only every Nth
          // healthy tick: the rejection will repeat until credentials change
          // (possibly outside this app), so checking each tick is wasted load,
          // but never checking would leave it failed after they are fixed.
          if (this.diagnosis !== 'auth_required' || ++authRecheck >= AUTH_RECHECK_TICKS) {
            authRecheck = 0;
            await this.markReady(gen, { message: undefined }, false);
          }
        }
      } else if (noteMiss() >= SUPERVISE_MISSES && gen === this.generation) {
        // The child-exit handler owns this bounded replacement handoff. It
        // will either attach or invalidate the generation before reporting a
        // crash, so this concurrent health loop must not race it.
        if (this.stage === 'attaching') {
          if (gen === this.generation) void tick();
          return;
        }
        // The listener keeps answering /health with something unhealthy. It is
        // neither busy nor dead, so say what is true instead of waiting
        // forever. `failed` is recoverable: the next healthy probe clears it,
        // and nothing is killed or respawned here.
        if (rejections >= SUPERVISE_REJECTIONS) {
          if (this.stage !== 'failed') {
            this.setStage('failed', {
              message: `The backend at ${this.baseUrl} answers /health but reports that it is not healthy.`,
              diagnosis: 'unhealthy',
            });
          }
          if (gen === this.generation) void tick();
          return;
        }
        // An answered-but-unhealthy probe is neither a stall nor a death, so it
        // must not announce "busy" or fall through to the crash path below
        // while the count above is still building.
        if (this.lastProbeOutcome === 'rejected') {
          if (gen === this.generation) void tick();
          return;
        }
        // Inference can monopolize Python's event loop longer than the health
        // deadline. A missed HTTP probe is not proof of process death. Keep
        // observing our live child; its exit handler owns crash reporting.
        //
        // Report this as `unresponsive`, NOT `failed` (#2430): the process is
        // demonstrably alive, so the failure channel would be a lie. `failed`
        // tears the workspace down behind an error gate, pauses every query
        // and dead-ends in-flight requests, all for a stall that the very next
        // probe clears. Announced once, then left to recover on its own.
        if (this.child && this.child.exitCode === null && this.child.signalCode === null) {
          if (this.stage !== 'unresponsive') {
            this.setStage('unresponsive', {
              message: `Backend is running but busy on port ${this.port}; it is not answering health checks right now. This resolves on its own once the current job finishes.`,
            });
          }
          if (gen === this.generation) void tick();
          return;
        }
        // An attached or remote backend has no child handle, but a refused
        // connection and a silent one differ: the kernel still accepts TCP for
        // a live process whose event loop is blocked, so timeouts alone are
        // never proof of death (#2601) — a long job can hold the loop for
        // hours. Stay `unresponsive`, keep probing, and let the user reconnect
        // from the status bar. Only consecutive refusals (the listener is
        // gone) declare it crashed.
        if (!this.managed && refusals < SUPERVISE_MISSES) {
          if (this.stage !== 'unresponsive') {
            this.setStage('unresponsive', {
              message: this.remoteUrl
                ? `Cannot confirm connectivity to the remote backend at ${this.baseUrl}: its health checks are timing out (a network problem, or the server is busy). Retrying automatically.`
                : `The external backend at ${this.baseUrl} is running but busy; it is not answering health checks right now. This resolves on its own once the current job finishes.`,
              // English above is for logs and bug reports; the renderer shows
              // the localized catalog string for this code.
              diagnosis: this.remoteUrl ? 'remote_unreachable' : undefined,
            });
          }
          if (gen === this.generation) void tick();
          return;
        }
        this.generation++;
        release();
        const wasManaged = this.managed;
        await this.killChild();
        this.setStage('crashed', {
          message: wasManaged
            ? `Backend stopped answering /health on port ${this.port}.`
            : `The external backend at ${this.baseUrl} stopped answering.`,
        });
        return;
      }
      if (gen === this.generation) void tick();
      else release();
    };
    void tick();
  }

  private async prepareDeliberateShutdown(): Promise<void> {
    if (!this.managed || this.remoteUrl) return;
    try {
      await fetch(`${this.baseUrl}/system/shutdown-intent`, {
        method: 'POST',
        headers: this.requestHeaders(),
        signal: AbortSignal.timeout(SHUTDOWN_INTENT_TIMEOUT_MS),
      });
    } catch {
      // Best effort: quitting must remain possible when the backend is wedged.
    }
  }

  private async killChild(deliberate = false): Promise<void> {
    const child = this.child;
    if (!child || child.pid === undefined || child.exitCode !== null) {
      this.child = null;
      return;
    }
    const pid = child.pid;
    const exited = new Promise<void>((r) => {
      child.once('exit', () => r());
    });
    if (deliberate) await this.prepareDeliberateShutdown();
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], {
        windowsHide: true,
      });
    } else {
      try {
        process.kill(-pid, 'SIGTERM');
      } catch {
        /* group already gone */
      }
      const result = await Promise.race([
        exited.then(() => 'exited'),
        delay(POSIX_SIGKILL_AFTER_MS),
      ]);
      if (result !== 'exited') {
        try {
          process.kill(-pid, 'SIGKILL');
        } catch {
          /* group already gone */
        }
      }
    }
    child.stdin?.end();
    await Promise.race([exited, delay(3000)]);
    if (this.child === child) this.child = null;
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
