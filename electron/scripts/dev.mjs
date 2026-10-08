import { spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

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

  if (cacheIsValid()) return plan.destinationExecutable;

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

  if (process.platform === 'darwin') {
    const electronPackage = require.resolve('electron/package.json');
    const { version: electronVersion } = JSON.parse(readFileSync(electronPackage, 'utf8'));
    const { version: appVersion } = JSON.parse(
      readFileSync(join(repoRoot, 'package.json'), 'utf8'),
    );
    env.ELECTRON_EXEC_PATH = prepareMacDevElectron({
      electronExecutable: env.ELECTRON_EXEC_PATH,
      electronVersion,
      appVersion,
      iconPath: join(electronRoot, 'build', 'icons', 'icon.icns'),
    });
  }

  const child = spawnProcess(process.execPath, [electronViteBin, 'dev', '--watch', ...args], {
    cwd: electronRoot,
    env,
    stdio: 'inherit',
  });
  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exitCode = code ?? 1;
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  launchElectronVite();
}
