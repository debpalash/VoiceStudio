import { execFileSync } from 'node:child_process';
import { chmodSync, existsSync, mkdtempSync, readdirSync, renameSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';
import { configureSnapLauncher } from './snap-launcher.mjs';

export const REQUIRED_SNAP_RUNTIME_PATHS = [
  'desktop-init.sh',
  'desktop-common.sh',
  'desktop-gnome-specific.sh',
  'usr/lib/x86_64-linux-gnu/libnss3.so',
];

const NESTED_TEMPLATE_PATTERN = /^snap-template-electron-.*\.tar$/;

export function verifySnapRuntimePayload(listing) {
  const nestedTemplate = listing.match(/squashfs-root\/(snap-template-electron-[^\s/]+\.tar)/)?.[1];
  if (nestedTemplate) {
    throw new Error(`Snap contains an unexpanded desktop runtime: ${nestedTemplate}`);
  }
  for (const path of REQUIRED_SNAP_RUNTIME_PATHS) {
    if (!listing.includes(`squashfs-root/${path}`)) {
      throw new Error(`Snap payload is missing desktop runtime path: ${path}`);
    }
  }
}

export function validateTemplateArchiveEntries(entries) {
  const normalized = entries
    .split(/\r?\n/)
    .map((entry) => entry.replace(/^\.\//, ''))
    .filter(Boolean);
  for (const entry of normalized) {
    if (entry.startsWith('/') || entry.split('/').includes('..')) {
      throw new Error(`Unsafe Snap template archive path: ${entry}`);
    }
  }
  for (const path of REQUIRED_SNAP_RUNTIME_PATHS) {
    if (!normalized.includes(path)) {
      throw new Error(`Snap template archive is missing: ${path}`);
    }
  }
}

export function repairSnapTemplatePayload(artifact) {
  const listing = execFileSync('unsquashfs', ['-ll', artifact], { encoding: 'utf8' });
  try {
    verifySnapRuntimePayload(listing);
    const command = execFileSync('unsquashfs', ['-cat', artifact, 'command.sh'], { encoding: 'utf8' });
    if (configureSnapLauncher(command) === command) return false;
  } catch (error) {
    if (!listing.includes('squashfs-root/snap-template-electron-')) throw error;
  }

  const workDir = mkdtempSync(resolve(dirname(artifact), '.snap-template-repair-'));
  const extracted = resolve(workDir, 'root');
  const repaired = resolve(workDir, basename(artifact));
  try {
    execFileSync('unsquashfs', ['-quiet', '-d', extracted, artifact], { stdio: 'inherit' });
    const archives = readdirSync(extracted).filter((name) => NESTED_TEMPLATE_PATTERN.test(name));
    if (archives.length > 1) {
      throw new Error(`Expected at most one nested Snap template archive, found: ${archives.join(', ')}`);
    }
    if (archives.length === 1) {
      const archive = resolve(extracted, archives[0]);
      const entries = execFileSync('tar', ['-tf', archive], { encoding: 'utf8' });
      validateTemplateArchiveEntries(entries);
      execFileSync('tar', ['-xf', archive, '-C', extracted], { stdio: 'inherit' });
      rmSync(archive);
    }
    const commandPath = resolve(extracted, 'command.sh');
    writeFileSync(commandPath, configureSnapLauncher(readFileSync(commandPath, 'utf8')));
    chmodSync(commandPath, 0o755);
    for (const script of REQUIRED_SNAP_RUNTIME_PATHS.slice(0, 3)) {
      chmodSync(resolve(extracted, script), 0o755);
    }
    execFileSync(
      'mksquashfs',
      [
        extracted,
        repaired,
        '-noappend',
        '-comp',
        'xz',
        '-no-xattrs',
        '-no-fragments',
        '-all-root',
        '-quiet',
      ],
      { stdio: 'inherit' },
    );
    const repairedListing = execFileSync('unsquashfs', ['-ll', repaired], { encoding: 'utf8' });
    verifySnapRuntimePayload(repairedListing);
    renameSync(repaired, artifact);
    return true;
  } finally {
    if (existsSync(workDir)) rmSync(workDir, { recursive: true, force: true });
  }
}

export function repairSnapArtifacts(artifactPaths) {
  for (const artifact of artifactPaths.filter((path) => path.endsWith('.snap'))) {
    if (repairSnapTemplatePayload(artifact)) {
      console.log(`Prepared the desktop runtime and strict-confinement launcher in ${basename(artifact)}`);
    }
  }
  return [];
}
