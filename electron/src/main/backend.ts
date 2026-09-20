import {
  installRuntime,
  promoteLegacyRuntimeCaches,
  runtimeCompatible,
  runtimeDependenciesReady,
  runtimeInstallInterrupted,
  runtimeReady,
  runtimePython,
  stageRuntimeSources,
  type RuntimeRegion,
} from './runtime-project';
import { CrashJournal } from './crash-journal';
import { spawn, spawnSync, type ChildProcess, type StdioOptions } from 'node:child_process';
import { EventEmitter } from 'node:events';
import {
  accessSync,
  constants,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { rm } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, delimiter, dirname, isAbsolute, join, resolve } from 'node:path';
import { app } from 'electron';
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
import { SetupProgressTracker, cleanProcessLine } from './setup-progress';

const DEFAULT_PORT = 3900;
const DEFAULT_BUDGET_S = 300;
const READY_POLL_MS = 500;
/** Allow another VoiceStudio shell to take ownership without showing a false crash. */
const REPLACEMENT_ATTACH_GRACE_MS = 10_000;
const SUPERVISE_POLL_MS = 2000;
const PROBE_TIMEOUT_MS = 1500;
const SHUTDOWN_INTENT_TIMEOUT_MS = 1000;
/** Consecutive supervisor probe misses (2 s apart) before a ready backend is declared gone. */
const SUPERVISE_MISSES = 3;
const LOG_RING_LINES = 200;
const LOG_TAIL_LINES = 40;
/** EX_CONFIG (sysexits.h): backend/main.py exits with it when the port is taken (#1223). */
const EXIT_PORT_IN_USE = 78;
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
  return Number.isInteger(raw) && raw > 0 && raw < 65536 ? raw : DEFAULT_PORT;
}

function startupBudgetMs(): number {
  const raw = Number(process.env.OMNIVOICE_STARTUP_BUDGET_S);
  return (Number.isFinite(raw) && raw >= 0 ? raw : DEFAULT_BUDGET_S) * 1000;
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

function findUv(): string | null {
  if (app.isPackaged && process.resourcesPath) {
    const bundled = bundledUvPath(process.resourcesPath);
    if (usableFile(bundled)) return bundled;
  }
  const names = process.platform === 'win32' ? ['uv.exe', 'uv'] : ['uv'];
  const home = homedir();
  // A GUI launch (Finder, Explorer, a .desktop file) does not see the shell's
  // PATH additions, so the standard uv install locations are checked too.
  const dirs = [
    ...(process.env.PATH ?? '').split(delimiter).filter(Boolean),
    join(home, '.local', 'bin'),
    join(home, '.cargo', 'bin'),
    ...(process.platform === 'win32' ? [] : ['/opt/homebrew/bin', '/usr/local/bin']),
  ];
  for (const dir of dirs) {
    for (const name of names) {
      const candidate = join(dir, name);
      if (usableFile(candidate)) return candidate;
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
    ) as { root?: unknown; owned?: unknown };
    return typeof parsed.root === 'string' && isAbsolute(parsed.root)
      ? { root: resolve(parsed.root), owned: parsed.owned === true }
      : null;
  } catch {
    return null;
  }
}

function storedRuntimeRoot(): string | null {
  return storedRuntimeLocation()?.root ?? null;
}

function writeRuntimeLocation(root: string, owned: boolean): void {
  const locationFile = join(app.getPath('userData'), RUNTIME_LOCATION_FILE);
  mkdirSync(dirname(locationFile), { recursive: true });
  writeFileSync(locationFile, JSON.stringify({ root: resolve(root), owned }), 'utf8');
}

function selectedRuntimeRoot(parent: string): string {
  const selected = resolve(parent);
  return basename(selected).toLocaleLowerCase('en-US') === 'voicestudio'
    ? selected
    : join(selected, 'VoiceStudio');
}

function samePath(left: string, right: string): boolean {
  const normalizedLeft = resolve(left);
  const normalizedRight = resolve(right);
  return process.platform === 'win32'
    ? normalizedLeft.toLocaleLowerCase('en-US') === normalizedRight.toLocaleLowerCase('en-US')
    : normalizedLeft === normalizedRight;
}

function resolveSpawnPlan(port: number, packagedProject?: string): SpawnPlan | { error: string } {
  const root = backendRoot();
  const override = parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD);
  if (override) return { argv: override, cwd: root };
  if (app.isPackaged) {
    const project = packagedProject ?? join(defaultRuntimeRoot(), 'project');
    return {
      argv: [runtimePython(project), '-m', ...UVICORN_ARGS, '--port', String(port)],
      cwd: project,
    };
  }
  const uv = findUv();
  const portArg = ['--port', String(port)];
  if (uv)
    return {
      argv: [uv, 'run', '--project', root, ...UVICORN_ARGS, ...portArg],
      cwd: root,
    };
  const python = venvPython(root);
  if (existsSync(python)) {
    return { argv: [python, '-m', ...UVICORN_ARGS, ...portArg], cwd: root };
  }
  return {
    error:
      `Neither uv nor a Python venv was found for ${root}. ` +
      'Install uv (https://docs.astral.sh/uv/) or run `uv sync` in the repo, then restart.',
  };
}

function childEnv(
  port: number,
  region: RuntimeRegion = 'auto',
  platform = process.platform,
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  env.PYTHONUNBUFFERED = '1';
  env.PYTHONUTF8 = '1';
  // Arms backend/core/parent_liveness.py: stdin EOF == "the shell is gone".
  env.OMNIVOICE_DESKTOP_CONTAINED = '1';
  env.OMNIVOICE_PORT = String(port);
  // #2215: the backend resolves uv as OMNIVOICE_BUNDLED_UV first and
  // `shutil.which("uv")` second. The packaged uv lives in resources/tools,
  // which is on nobody's PATH, and a GUI launch does not inherit the shell's
  // PATH either — so `which` missed a uv the shell had already located, and
  // every one-click sidecar install died at preflight with "uv was not found"
  // while the binary sat inside the app bundle. findUv() knows where to look;
  // hand the answer over instead of keeping it. An explicit override from the
  // environment still wins.
  if (!env.OMNIVOICE_BUNDLED_UV) {
    const uv = findUv();
    if (uv) env.OMNIVOICE_BUNDLED_UV = uv;
  }
  if (region === 'china') env.HF_ENDPOINT ??= 'https://hf-mirror.com';
  if (platform === 'win32') {
    env.TORCHDYNAMO_DISABLE = '1';
    env.HF_HUB_DISABLE_SYMLINKS = '1';
    env.HF_HUB_DISABLE_SYMLINKS_WARNING = '1';
    // MKL's Fortran runtime aborts the child on console CLOSE/LOGOFF events (#1153).
    env.FOR_DISABLE_CONSOLE_CTRL_HANDLER ??= '1';
  }
  return env;
}

export function managedBackendSpawnOptions(
  port: number,
  region: RuntimeRegion = 'auto',
  platform = process.platform,
): { env: NodeJS.ProcessEnv; stdio: StdioOptions; drainFd: number | null } {
  const drainFd = platform === 'win32' ? null : 3;
  const env = childEnv(port, region, platform);
  if (drainFd !== null) env.OMNIVOICE_DESKTOP_DRAIN_FD = String(drainFd);
  return {
    env,
    stdio: drainFd === null ? ['pipe', 'pipe', 'pipe'] : ['pipe', 'pipe', 'pipe', 'pipe'],
    drainFd,
  };
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
  readonly port = resolvePort();
  private readonly localBaseUrl = `http://127.0.0.1:${this.port}`;
  private remoteUrl = loadRemoteBackend(this.remotePath);
  private remoteSession: RemoteSession | null = null;
  private testedRemote: { url: string; session: RemoteSession | null } | null = null;

  private stage: BackendStage = 'idle';
  private managed = false;
  private message: string | undefined;
  private exitCode: number | null | undefined;
  private startedAt = Date.now();
  private child: ChildProcess | null = null;
  private readonly log: string[] = [];
  /** Bumped on every start/shutdown so stale poll loops and exit handlers no-op. */
  private generation = 0;
  /** A generation owns at most one health loop, even if readiness is observed twice. */
  private supervisingGeneration: number | null = null;
  private shuttingDown = false;
  private setupIssue: BackendStatus['setupIssue'];
  private runtimeInterrupted = false;
  private setupPhase: BackendStatus['setupPhase'] = 'checking';
  private readonly setupProgress = new SetupProgressTracker();
  private installation: AbortController | null = null;
  private cleaningRuntime = false;
  private runtimeProject: string | null = null;
  private runtimeRegion: RuntimeRegion = loadRuntimeRegion();

  get baseUrl(): string {
    return this.remoteUrl || this.localBaseUrl;
  }

  get connection(): BackendConnection {
    return {
      remote: Boolean(this.remoteUrl),
      url: this.baseUrl,
      authenticated: Boolean(this.activeSession()),
    };
  }

  requestHeaders(): Record<string, string> {
    const session = this.activeSession();
    return session ? { Authorization: `Bearer ${session.token}` } : {};
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
          }
        : {}),
    };
    if (this.stage === 'setup_required' && this.setupIssue) status.setupIssue = this.setupIssue;
    if (this.stage === 'setup_required' && this.runtimeInterrupted)
      status.runtimeInterrupted = true;
    if (this.stage === 'installing') {
      status.setupPhase = this.setupPhase;
      status.setupProgress = this.setupProgress.snapshot();
    }
    if (this.message !== undefined) status.message = this.message;
    if (this.exitCode !== undefined) status.exitCode = this.exitCode;
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
    this.setStage('attaching', { managed: false, message: undefined });
    try {
      if (await this.probe()) {
        if (gen !== this.generation) return;
        this.runtimeInterrupted = false;
        this.setStage('ready');
        this.supervise(gen);
        return;
      }
      if (gen !== this.generation) return;

      if (this.remoteUrl) {
        this.setStage('failed', {
          managed: false,
          message: `Could not reach the configured remote backend at ${this.remoteUrl}.`,
        });
        return;
      }

      if (process.env.VOICESTUDIO_SKIP_BACKEND === '1') {
        this.setStage('attaching', {
          message: `VOICESTUDIO_SKIP_BACKEND=1 — waiting for an external backend on port ${this.port}`,
        });
        void this.waitUntilReady(gen, Number.POSITIVE_INFINITY);
        return;
      }

      if (app.isPackaged && !parseBackendCmdOverride(process.env.OMNIVOICE_BACKEND_CMD)) {
        const { project, ready } = await this.resolveRuntimeProject();
        if (!ready) {
          if (gen === this.generation) {
            this.runtimeInterrupted = await runtimeInstallInterrupted(project);
            this.setStage('setup_required');
          }
          return;
        }
        this.runtimeInterrupted = false;
        await stageRuntimeSources(backendRoot(), project);
        if (gen !== this.generation) return;
      }
      const plan = resolveSpawnPlan(this.port, this.runtimeProject ?? undefined);
      if ('error' in plan) {
        this.setStage('failed', { message: plan.error });
        return;
      }
      this.spawnChild(plan, gen);
      if (gen !== this.generation || this.stage !== 'starting') return;
      void this.waitUntilReady(gen, startupBudgetMs());
    } catch (error) {
      if (gen !== this.generation) return;
      const message = errorMessage(error);
      this.pushLog('err', message);
      this.setStage('failed', { message });
    }
  }

  /** Explicit first-run action. Installation never happens in start()/restart(). */
  async setupRuntime(): Promise<void> {
    if (
      !app.isPackaged ||
      this.installation ||
      this.cleaningRuntime ||
      this.stage !== 'setup_required'
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
    this.setupIssue = undefined;
    this.runtimeInterrupted = false;
    this.setupPhase = 'checking';
    this.setupProgress.reset();
    this.setStage('installing', { message: undefined });
    try {
      const reusable =
        ((await runtimeReady(backendRoot(), project)) ||
          (await runtimeCompatible(backendRoot(), project))) &&
        (await runtimeDependenciesReady(project));
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
        existsSync(runtimeRoot)
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
        const fallbackReusable =
          ((await runtimeReady(backendRoot(), project)) ||
            (await runtimeCompatible(backendRoot(), project))) &&
          (await runtimeDependenciesReady(project));
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
        (command, args, cwd, env) =>
          new Promise<string>((resolve, reject) => {
            if (controller.signal.aborted) {
              reject(controller.signal.reason);
              return;
            }
            const child = spawn(command, args, {
              cwd,
              env: { ...childEnv(this.port, this.runtimeRegion), ...env },
              windowsHide: true,
              detached: process.platform !== 'win32',
              stdio: ['pipe', 'pipe', 'pipe'],
            });
            this.child = child;
            let capturedStdout = '';
            child.stdin?.on('error', () => {});
            child.stdout?.on('data', (chunk: Buffer | string) => {
              capturedStdout = (capturedStdout + chunk.toString()).slice(-65_536);
            });
            this.attachLineReader(child.stdout, 'out');
            this.attachLineReader(child.stderr, 'err');
            child.on('error', reject);
            child.on('close', (code) => {
              if (this.child === child) this.child = null;
              if (code === 0) resolve(capturedStdout);
              else reject(new Error(`Runtime setup exited with code ${code}`));
            });
          }),
        controller.signal,
        (phase) => {
          if (gen !== this.generation) return;
          this.setupPhase = phase;
          this.emitStatus();
        },
        this.runtimeRegion,
      );
      if (gen === this.generation) await this.start();
    } catch (error) {
      if (gen === this.generation) {
        const code = (error as NodeJS.ErrnoException)?.code;
        this.setupIssue =
          code === 'ENOSPC'
            ? 'space'
            : ['EACCES', 'EPERM', 'EROFS'].includes(code || '')
              ? 'access'
              : undefined;
        this.runtimeInterrupted = await runtimeInstallInterrupted(project);
        this.pushLog('err', errorMessage(error));
        this.setStage('setup_required', { message: errorMessage(error) });
      }
    } finally {
      if (this.installation === controller) this.installation = null;
    }
  }

  /** Remove only an Electron-owned Python project, then perform a fresh install. */
  async cleanSetupRuntime(): Promise<void> {
    if (
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
        this.pushLog('err', errorMessage(error));
        this.setStage('setup_required', { message: errorMessage(error) });
      }
      return;
    } finally {
      this.cleaningRuntime = false;
    }
    if (gen !== this.generation) return;
    this.setupIssue = undefined;
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

  setRuntimeLocation(parent: string | null): { path: string; custom: boolean } {
    if (
      !app.isPackaged ||
      this.stage !== 'setup_required' ||
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
      writeRuntimeLocation(root, false);
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

  websocketUrl(path: '/ws/transcribe' | '/ws/events' | '/ws/tts'): Promise<string> {
    return remoteWebSocketUrl(this.baseUrl, path, this.remoteUrl ? this.activeSession() : null);
  }

  private setStage(
    stage: BackendStage,
    patch: { managed?: boolean; message?: string | undefined } = {},
  ): void {
    this.stage = stage;
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
    for (const project of new Set(candidates.map((candidate) => resolve(candidate)))) {
      if (
        ((await runtimeReady(bundle, project)) || (await runtimeCompatible(bundle, project))) &&
        (await runtimeDependenciesReady(project))
      ) {
        this.runtimeProject = project;
        if (project !== own && project !== configured)
          this.pushLog('out', `Reusing compatible Tauri runtime: ${project}`);
        return { project, ready: true };
      }
    }
    this.runtimeProject = configured ?? own;
    return { project: this.runtimeProject, ready: false };
  }

  private emitStatus(): void {
    this.emit('status', this.status);
  }

  private pushLog(stream: 'out' | 'err', line: string): void {
    line = cleanProcessLine(line);
    if (!line) return;
    this.log.push(line);
    if (this.log.length > LOG_RING_LINES) this.log.splice(0, this.log.length - LOG_RING_LINES);
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
      if (pending.length > 0) this.pushLog(stream, pending);
      pending = '';
    };
    readable.setEncoding('utf8');
    readable.on('data', (chunk: string) => {
      pending += chunk;
      const lines = pending.split(/[\r\n]+/);
      pending = lines.pop() ?? '';
      for (const line of lines) if (line.length > 0) this.pushLog(stream, line);
    });
    readable.on('end', flushPending);
    readable.on('error', (error: unknown) => {
      flushPending();
      if (!isExpectedPipeClose(error)) {
        this.pushLog('err', `Backend ${stream} stream failed: ${errorMessage(error)}`);
      }
    });
  }

  private spawnChild(plan: SpawnPlan, gen: number): void {
    const [command, ...args] = plan.argv;
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
        message: `Could not start the backend: ${errorMessage(err)}`,
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
        message: `Could not start the backend: ${errorMessage(err)}`,
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
        this.pushLog('out', 'Attached to the replacement VoiceStudio backend.');
        this.setStage('ready', { managed: false, message: undefined });
        this.supervise(gen);
        return;
      }
      if (Date.now() >= deadline) break;
      await delay(READY_POLL_MS);
    }
    if (gen !== this.generation || this.shuttingDown) return;

    // End every readiness/supervisor loop for the dead ownership generation
    // before publishing the terminal result.
    this.generation++;
    this.crashes.record(code, signal, Date.now() - this.startedAt, this.log);
    this.exitCode = code;
    if (code === EXIT_PORT_IN_USE) {
      this.setStage('port_in_use', {
        message: `Port ${this.port} is already in use by another process. Stop it or set OMNIVOICE_PORT.`,
      });
      return;
    }
    const lastLine = this.log.at(-1);
    const why = signal ? `signal ${signal}` : `exit code ${code}`;
    this.setStage('crashed', {
      message: `Backend exited unexpectedly (${why}).${lastLine ? ` Last output: ${lastLine}` : ''}`,
    });
  }

  private async probe(): Promise<boolean> {
    try {
      // This runs for the entire desktop session. Use the canonical, tiny
      // liveness response instead of repeatedly serializing full hardware,
      // settings and path information from /system/info.
      const res = await fetch(`${this.baseUrl}/health`, {
        headers: this.requestHeaders(),
        signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
      });
      if (!res.ok) return false;
      const body: unknown = await res.json();
      return (
        typeof body === 'object' &&
        body !== null &&
        (body as { status?: unknown }).status === 'ok' &&
        typeof (body as { version?: unknown }).version === 'string'
      );
    } catch {
      return false;
    }
  }

  private async waitUntilReady(gen: number, budgetMs: number): Promise<void> {
    const deadline = this.startedAt + budgetMs;
    while (gen === this.generation) {
      if (await this.probe()) {
        if (gen !== this.generation) return;
        this.setStage('ready', { message: undefined });
        this.supervise(gen);
        return;
      }
      if (gen !== this.generation || this.stage !== 'starting') {
        if (gen === this.generation && this.stage === 'attaching') {
          await delay(READY_POLL_MS);
          continue;
        }
        return;
      }
      if (Date.now() > deadline) {
        this.generation++;
        await this.killChild();
        this.setStage('failed', {
          message:
            `Backend did not answer on port ${this.port} within ${Math.round(budgetMs / 1000)} s ` +
            '(OMNIVOICE_STARTUP_BUDGET_S). Check the log above.',
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
        if (gen === this.generation && this.stage === 'failed') {
          this.setStage('ready', { message: undefined });
        }
      } else if (++misses >= SUPERVISE_MISSES && gen === this.generation) {
        // The child-exit handler owns this bounded replacement handoff. It
        // will either attach or invalidate the generation before reporting a
        // crash, so this concurrent health loop must not race it.
        if (this.stage === 'attaching') {
          if (gen === this.generation) void tick();
          return;
        }
        // Inference can monopolize Python's event loop longer than the health
        // deadline. A missed HTTP probe is not proof of process death. Keep
        // observing our live child; its exit handler owns crash reporting.
        if (this.child && this.child.exitCode === null && this.child.signalCode === null) {
          if (misses === SUPERVISE_MISSES) {
            this.setStage('failed', {
              message: `Backend is running but temporarily not responding on port ${this.port}. Waiting for recovery.`,
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
