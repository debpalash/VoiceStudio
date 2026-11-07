import { spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { assertSupportedNode } from './node-version.mjs';
import {
  activeMacDevBundles,
  holdMacDevBundle,
  macDevLeaseDirectory,
  withMacDevCacheLock,
} from './mac-dev-cache.mjs';

// Check before importing configuration with syntax that older Node cannot parse.
const isEntryPoint = process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isEntryPoint) {
  try {
    assertSupportedNode();
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
const { DEFAULT_ELECTRON_DEV_PORT, DEFAULT_ELECTRON_PROXY_PORT } =
  await import('../../config/app-config.mjs');
const { clearDevPorts } = await import('../../scripts/clear-dev-ports.mjs');

const APP_NAME = 'VoiceStudio';
const here = dirname(fileURLToPath(import.meta.url));
const electronRoot = resolve(here, '..');
const repoRoot = resolve(electronRoot, '..');
const require = createRequire(import.meta.url);
const defaultCacheRoot = join(electronRoot, 'node_modules', '.cache', 'voicestudio-electron-dev');

export function createMacDevBundlePlan({
  electronExecutable,
  electronVersion,
  appVersion,
  architecture = process.arch,
  cacheFingerprint = '',
  cacheRoot = defaultCacheRoot,
}) {
  const sourceBundle = resolve(dirname(electronExecutable), '../..');
  const cacheKey = [electronVersion, appVersion, architecture, cacheFingerprint]
    .filter(Boolean)
    .join('-');
  const destinationRoot = join(resolve(cacheRoot), cacheKey);
  const destinationBundle = join(destinationRoot, `${APP_NAME}.app`);

  return {
    sourceBundle,
    destinationRoot,
    destinationBundle,
    // Keep Electron's executable name so electron-vite continues to detect a
    // development launch. macOS takes the visible name from the app bundle.
    destinationExecutable: join(
      destinationBundle,
      'Contents',
      'MacOS',
      basename(electronExecutable),
    ),
    infoPlist: join(destinationBundle, 'Contents', 'Info.plist'),
    resourcesDirectory: join(destinationBundle, 'Contents', 'Resources'),
    manifest: join(destinationRoot, 'brand-manifest.json'),
  };
}

function run(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  if (result.status !== 0) {
    const detail = result.stderr?.trim() || result.stdout?.trim() || `exit ${result.status}`;
    throw new Error(`${command} failed: ${detail}`);
  }
}

function replacePlistValue(infoPlist, key, value, runCommand = run) {
  runCommand('plutil', ['-replace', key, '-string', value, infoPlist]);
}

export function prepareMacDevElectron({
  electronExecutable,
  electronVersion,
  appVersion,
  iconPath,
  cacheRoot = defaultCacheRoot,
  runCommand = run,
}) {
  const icon = statSync(iconPath);
  const cacheFingerprint = `${icon.size}-${String(icon.mtimeMs).replace('.', '_')}`;
  const plan = createMacDevBundlePlan({
    electronExecutable,
    electronVersion,
    appVersion,
    cacheFingerprint,
    cacheRoot,
  });
  const expectedManifest = JSON.stringify({
    electronVersion,
    appVersion,
    architecture: process.arch,
    iconSize: icon.size,
    iconModified: icon.mtimeMs,
  });

  const cacheIsValid = () =>
    existsSync(plan.destinationExecutable) &&
    existsSync(plan.manifest) &&
    readFileSync(plan.manifest, 'utf8') === expectedManifest;

  if (cacheIsValid()) {
    return plan.destinationExecutable;
  }

  mkdirSync(cacheRoot, { recursive: true });
  const stagingRoot = mkdtempSync(join(cacheRoot, '.staging-'));
  const stagingBundle = join(stagingRoot, `${APP_NAME}.app`);
  rmSync(stagingRoot, { recursive: true, force: true });
  mkdirSync(stagingRoot);

  try {
    // APFS clone-copy keeps this fast and avoids duplicating Electron's full
    // framework bundle. The copied bundle is then re-signed after branding.
    runCommand('cp', ['-cR', plan.sourceBundle, stagingBundle]);
    const infoPlist = join(stagingBundle, 'Contents', 'Info.plist');
    replacePlistValue(infoPlist, 'CFBundleDisplayName', APP_NAME, runCommand);
    replacePlistValue(infoPlist, 'CFBundleName', APP_NAME, runCommand);
    replacePlistValue(infoPlist, 'CFBundleIdentifier', 'com.voicestudio.desktop.dev', runCommand);
    replacePlistValue(infoPlist, 'CFBundleIconFile', `${APP_NAME}.icns`, runCommand);
    replacePlistValue(infoPlist, 'CFBundleShortVersionString', appVersion, runCommand);
    replacePlistValue(infoPlist, 'CFBundleVersion', appVersion, runCommand);
    copyFileSync(iconPath, join(stagingBundle, 'Contents', 'Resources', `${APP_NAME}.icns`));
    runCommand('codesign', ['--force', '--deep', '--sign', '-', stagingBundle]);
    writeFileSync(join(stagingRoot, 'brand-manifest.json'), expectedManifest);

    // Reaching here means the cache failed validation (e.g. its executable was
    // lost). The invalid remnant still occupies the target, so the rename below
    // would fail with ENOTEMPTY on every run; clear it first. A concurrent
    // launcher may have just published a valid bundle, which we keep instead.
    if (cacheIsValid()) return plan.destinationExecutable;
    rmSync(plan.destinationRoot, { recursive: true, force: true });

    try {
      renameSync(stagingRoot, plan.destinationRoot);
    } catch (error) {
      if (
        !(
          error instanceof Error &&
          'code' in error &&
          (error.code === 'EEXIST' || error.code === 'ENOTEMPTY')
        )
      ) {
        throw error;
      }
      if (!cacheIsValid()) {
        throw new Error('Concurrent macOS development bundle creation produced an invalid cache');
      }
    }
  } finally {
    rmSync(stagingRoot, { recursive: true, force: true });
  }

  return plan.destinationExecutable;
}

/** Reserve a bundle before returning it, including the gap before Vite spawns. */
export function prepareMacDevSession(options) {
  const cacheRoot = options.cacheRoot ?? defaultCacheRoot;
  return withMacDevCacheLock(
    cacheRoot,
    (directory) => {
      const executable = prepareMacDevElectron(options);
      const bundleRoot = resolve(dirname(executable), '../../..');
      const lease = holdMacDevBundle(directory, bundleRoot);
      try {
        pruneOldBundles(cacheRoot, bundleRoot, { leaseDirectory: directory });
        return { executable, ...lease };
      } catch (error) {
        lease.release();
        throw error;
      }
    },
    options.leaseDirectory,
  );
}

/**
 * Production callers hold the coordination lock throughout preparation,
 * reservation and pruning. Active leases protect both running and not-yet-
 * spawned dev servers; old launchers/orphan Electron apps are checked too.
 */
export function pruneOldBundles(cacheRoot, keep, { leaseDirectory } = {}) {
  let entries, root, selected, protectedRoots;
  try {
    root = realpathSync(cacheRoot);
    selected = realpathSync(keep);
    protectedRoots = activeMacDevBundles(leaseDirectory ?? macDevLeaseDirectory(root));
    if (!protectedRoots) return;
    if (process.platform === 'darwin') {
      const result = spawnSync('ps', ['-axo', 'pid=,command='], { encoding: 'utf8' });
      if (result.status !== 0) return; // Unknown usage is not safe to delete.
      for (const line of result.stdout.split(/\r?\n/)) {
        const command = /^\s*\d+\s+(.*)$/.exec(line)?.[1];
        const marker = '/VoiceStudio.app/Contents/MacOS/';
        if (!command?.includes(marker)) continue;
        const end = command.indexOf(' ', command.indexOf(marker) + marker.length);
        const executable = end < 0 ? command : command.slice(0, end);
        // Resolve symlinked node_modules paths used by another worktree.
        try {
          if (realpathSync(executable).startsWith(root + '/')) return;
        } catch {
          /* It may be a different checkout or an already-exited app. */
        }
      }
    }
    entries = readdirSync(root, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
    const path = join(root, entry.name);
    if (path === selected || protectedRoots.has(path)) continue;
    try {
      rmSync(path, { recursive: true, force: true });
    } catch {
      /* A stale cache is optional cleanup, never a launch prerequisite. */
    }
  }
}

/**
 * Ports only a `bun dev` instance uses: the Electron main's private dev proxy
 * (listened on by the Electron app itself) and the renderer dev server. The
 * backend port is left alone: an installed VoiceStudio may own it, and the dev
 * app falls back to another port anyway.
 */
export function devInstancePorts(env = process.env) {
  const rawProxy = env.VOICESTUDIO_ELECTRON_PROXY_PORT?.trim();
  const renderer =
    Number(env.OMNIVOICE_UI_PORT || env.VOICESTUDIO_UI_PORT) || DEFAULT_ELECTRON_DEV_PORT;
  // `0` lets the OS pick the proxy port (electronProxyPort), so there is none to free.
  if (rawProxy === '0') return [renderer];
  return [Number(rawProxy) || DEFAULT_ELECTRON_PROXY_PORT, renderer];
}

/**
 * The newest `bun dev` wins: stop an earlier dev instance of this checkout
 * (its Electron app first, which also holds the single-instance lock, then its
 * dev server) instead of failing with "Port 3902 is already in use".
 * clearDevPorts only stops processes that belong to this checkout and refuses
 * anything else, so another project on the same port is reported, not killed.
 */
export async function replacePreviousDevInstance(
  env = process.env,
  clear = clearDevPorts,
  stopApps = stopLeftoverDevApps,
) {
  if (env.VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES === '1') return;
  // The app first, through its normal quit path, and only once it has fully
  // exited the ports: an app still flushing its windows holds the proxy port
  // longer than clearDevPorts waits.
  await stopApps();
  await clear(devInstancePorts(env));
}

/**
 * Where this checkout's development Electron runs from: the branded bundle
 * cache on macOS, Electron's own dist folder elsewhere. Only `bun dev` (and
 * this checkout's Electron tests) run binaries from there.
 */
export function devElectronLocation(platform = process.platform) {
  const location =
    platform === 'darwin'
      ? defaultCacheRoot
      : join(electronRoot, 'node_modules', 'electron', 'dist');
  // Trailing separator: a sibling such as `…-dev-old` must never match.
  return location + (platform === 'win32' ? '\\' : '/');
}

function listProcesses(platform = process.platform) {
  if (platform === 'win32') {
    const result = spawnSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        'Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId) $($_.ExecutablePath)" }',
      ],
      { encoding: 'utf8' },
    );
    return result.status === 0 ? result.stdout : '';
  }
  const result = spawnSync('ps', ['-axo', 'pid=,command='], { encoding: 'utf8' });
  return result.status === 0 ? result.stdout : '';
}

/** Pids of processes whose command (or executable) lives under `location`. */
export function devElectronPids(listing, location, windows = process.platform === 'win32') {
  const prefix = windows ? location.toLowerCase().replaceAll('/', '\\') : location;
  const pids = [];
  for (const line of listing.split(/\r?\n/)) {
    const match = /^\s*(\d+)\s+(.*)$/.exec(line);
    if (!match) continue;
    const command = windows ? match[2].toLowerCase() : match[2];
    if (command.startsWith(prefix) && Number(match[1]) !== process.pid) pids.push(Number(match[1]));
  }
  return pids;
}

/**
 * SIGTERM enters Electron's normal quit path on macOS and Linux. On Windows
 * process.kill terminates outright, so the polite step there is taskkill
 * without /F, which asks the windows to close; SIGKILL stays the fallback.
 */
export function signalProcess(pid, signal, platform = process.platform, run = spawnSync) {
  if (platform === 'win32' && signal === 'SIGTERM') {
    run('taskkill', ['/PID', String(pid)], { encoding: 'utf8' });
    return;
  }
  process.kill(pid, signal);
}

/**
 * Ask this checkout's running dev Electron to quit and wait for it to exit.
 * Electron's quit does not always finish, which would leave a window holding
 * the single-instance lock, so it is forced after the timeout.
 */
export async function stopLeftoverDevApps({
  location = devElectronLocation(),
  list = listProcesses,
  kill = signalProcess,
  sleep = (ms) => new Promise((done) => setTimeout(done, ms)),
  // Electron's orderly quit allows 2.5s for window persistence and 5s for teardown.
  timeoutMs = 12000,
} = {}) {
  const leftovers = () => devElectronPids(list(), location);
  const signal = (pids, name) => {
    for (const pid of pids) {
      try {
        kill(pid, name);
      } catch (error) {
        if (error?.code !== 'ESRCH') throw error;
      }
    }
  };
  let pids = leftovers();
  if (!pids.length) return;
  signal(pids, 'SIGTERM');
  for (let waited = 0; waited < timeoutMs && pids.length; waited += 250) {
    await sleep(250);
    pids = leftovers();
  }
  // Re-listed just above: these still run from the dev Electron location.
  signal(pids, 'SIGKILL');
}

/**
 * electron-vite arguments for this launcher. `bun run dev` watches sources;
 * `bun run start` / `bun run preview` (`dev.mjs preview`) run the built app.
 * Both need the Electron binary resolved first, so neither may call
 * electron-vite directly (package-scripts test).
 */
export function electronViteArgs(args) {
  if (args[0] === 'preview') return ['preview', ...args.slice(1)];
  return ['dev', '--watch', ...args];
}

export function launchElectronVite(
  args = process.argv.slice(2),
  spawnProcess = spawn,
  resolveElectronExecutable = () => require('electron'),
) {
  const electronVitePackage = require.resolve('electron-vite/package.json');
  const { bin } = JSON.parse(readFileSync(electronVitePackage, 'utf8'));
  const electronViteBin = resolve(dirname(electronVitePackage), bin['electron-vite']);
  const env = { ...process.env };
  // Electron 42+ downloads lazily through its public entry point. electron-vite
  // reads path.txt directly, so resolve the binary before it starts on every OS.
  env.ELECTRON_EXEC_PATH ||= resolveElectronExecutable();
  // Inherited from editors/tools that run Electron as Node (VS Code's extension
  // host exports it); left set, the app's Electron starts as plain Node and
  // fails on its first `electron` import.
  delete env.ELECTRON_RUN_AS_NODE;

  let bundleSession;
  if (process.platform === 'darwin') {
    const electronPackage = require.resolve('electron/package.json');
    const { version: electronVersion } = JSON.parse(readFileSync(electronPackage, 'utf8'));
    const { version: appVersion } = JSON.parse(
      readFileSync(join(repoRoot, 'package.json'), 'utf8'),
    );
    bundleSession = prepareMacDevSession({
      electronExecutable: env.ELECTRON_EXEC_PATH,
      electronVersion,
      appVersion,
      iconPath: join(electronRoot, 'build', 'icons', 'icon.icns'),
    });
    env.ELECTRON_EXEC_PATH = bundleSession.executable;
  }

  let child;
  try {
    child = spawnProcess(process.execPath, [electronViteBin, ...electronViteArgs(args)], {
      cwd: electronRoot,
      env,
      // Vite holds this kernel lease across Electron main-process restarts,
      // even if this wrapper is killed before its normal shutdown handler.
      stdio: bundleSession ? ['inherit', 'inherit', 'inherit', bundleSession.fd] : 'inherit',
    });
  } catch (error) {
    bundleSession?.release();
    throw error;
  }
  // Stopping this launcher must stop its dev server too; otherwise the server
  // outlives it and keeps the port.
  const forwarded = ['SIGINT', 'SIGTERM', 'SIGHUP'];
  const forward = (signal) => child.kill(signal);
  for (const signal of forwarded) process.on(signal, forward);
  const release = () => {
    bundleSession?.release();
    for (const name of forwarded) process.off(name, forward);
  };
  child.on('error', (error) => {
    release();
    console.error(`Could not start the development server: ${error.message}`);
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    release();
    if (signal) process.kill(process.pid, signal);
    else process.exitCode = code ?? 1;
  });
}

if (isEntryPoint) {
  try {
    await replacePreviousDevInstance();
  } catch (error) {
    console.error(`Could not free the development ports: ${error.message}`);
    process.exit(1);
  }
  launchElectronVite();
}
