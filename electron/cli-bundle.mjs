// Build the public command from the Python package; users need no system Python.
import { execFileSync } from 'node:child_process';
import {
  chmodSync,
  cpSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Arch } from 'builder-util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const CLI_BUILD_REQUIREMENT = 'pyinstaller==6.22.3';

export function cliBuildArguments(work, platform = process.platform) {
  // Match the backend's supported x64 interpreter on Windows ARM too.
  const python = platform === 'win32' ? 'cpython-3.11-windows-x86_64-none' : '3.11';
  return [
    'tool',
    'run',
    '--python',
    python,
    '--from',
    CLI_BUILD_REQUIREMENT,
    '--with',
    join(root, 'cli'),
    'python',
    join(root, 'scripts/freeze_cli.py'),
    '--noconfirm',
    '--clean',
    '--onedir',
    '--console',
    '--name',
    'voicestudio',
    '--collect-data',
    'voicestudio_cli',
    '--copy-metadata',
    'voicestudio-cli',
    '--distpath',
    join(work, 'dist'),
    '--workpath',
    join(work, 'work'),
    '--specpath',
    work,
    join(root, 'cli/frozen_main.py'),
  ];
}

export function wirePackagedCli(context, bundle) {
  const platform = context.electronPlatformName;
  const resources = context.packager.getResourcesDir(context.appOutDir);
  const destination = join(resources, 'cli');
  cpSync(bundle, destination, { recursive: true });
  let desktop;
  if (platform === 'linux') {
    desktop = join(context.appOutDir, 'voicestudio-desktop');
    const publicEntry = join(context.appOutDir, context.packager.executableName);
    renameSync(publicEntry, desktop);
    writeFileSync(
      publicEntry,
      '#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/resources/cli/voicestudio" "$@"\n',
    );
    chmodSync(publicEntry, 0o755);
  } else if (platform === 'darwin') {
    desktop = join(
      context.appOutDir,
      `${context.packager.appInfo.productFilename}.app`,
      'Contents/MacOS',
      context.packager.appInfo.productFilename,
    );
  } else {
    desktop = join(context.appOutDir, `${context.packager.appInfo.productFilename}.exe`);
    cpSync(join(root, 'electron/packaging/cli-path.ps1'), join(destination, 'path.ps1'));
  }
  writeFileSync(
    join(destination, 'desktop.json'),
    JSON.stringify(
      {
        executable: relative(destination, desktop),
        version: context.packager.appInfo.version,
        application: relative(
          destination,
          platform === 'darwin'
            ? join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
            : desktop,
        ),
        method: platform === 'darwin' ? 'dmg' : platform === 'win32' ? 'nsis' : 'directory',
        arch: Arch[context.arch] ?? process.arch,
        distribution: context.packager.config?.extraMetadata?.voicestudioDistribution ?? 'direct',
      },
      null,
      2,
    ) + '\n',
  );
  return join(destination, `voicestudio${platform === 'win32' ? '.exe' : ''}`);
}

export async function packageCli(context) {
  if (
    context.electronPlatformName !== process.platform ||
    (Arch[context.arch] !== process.arch && process.platform !== 'win32')
  ) {
    throw new Error('Build the CLI on a runner matching the desktop target OS and architecture.');
  }
  const work = mkdtempSync(join(tmpdir(), 'voicestudio-cli-build-'));
  try {
    execFileSync(process.env.VOICESTUDIO_BUILD_UV || 'uv', cliBuildArguments(work), {
      cwd: root,
      stdio: 'inherit',
      windowsHide: true,
    });
    const command = wirePackagedCli(context, join(work, 'dist/voicestudio'));
    if (context.electronPlatformName === 'win32') {
      const signFiles = async (directory) => {
        for (const item of readdirSync(directory, { withFileTypes: true })) {
          const file = join(directory, item.name);
          if (item.isDirectory()) await signFiles(file);
          else if (/\.(exe|dll|pyd)$/i.test(item.name)) await context.packager.signIf(file);
        }
      };
      await signFiles(dirname(command));
    }
    const version = JSON.parse(execFileSync(command, ['version', '--json'], { encoding: 'utf8' }));
    const expected = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version;
    if (version.version !== expected)
      throw new Error('Bundled command reports a different application version.');
    execFileSync(command, ['--help'], { stdio: 'pipe' });
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}
