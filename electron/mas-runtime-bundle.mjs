import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { codesignArgs, isMachO, signMac } from './omnivoice-tts-bundle.mjs';

/** Sign executable code, not binary model/data assets or static archives. */
export function runtimeSignTargets(root) {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const file = join(root, entry.name);
    if (entry.isDirectory()) return runtimeSignTargets(file);
    return entry.isFile() && isMachO(file) ? [file] : [];
  });
}

function inventoryPaths(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    return entry.isDirectory() ? inventoryPaths(path) : [path];
  });
}

function digest(file) {
  const hash = createHash('sha256');
  const fd = openSync(file, 'r');
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    let bytes;
    while ((bytes = readSync(fd, buffer, 0, buffer.length, null)))
      hash.update(buffer.subarray(0, bytes));
    return hash.digest('hex');
  } finally {
    closeSync(fd);
  }
}

/** Validate the complete staged/signed inventory without treating it as licence clearance. */
export function verifyRuntimeInventory(root, lock, arch) {
  root = realpathSync(root);
  const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
  if (
    manifest.schemaVersion !== 1 ||
    manifest.arch !== arch ||
    manifest.lockSha256 !== digest(lock) ||
    !manifest.files ||
    typeof manifest.files !== 'object'
  )
    throw new Error('Bundled runtime provenance mismatch');
  const inside = (path) => {
    const child = relative(root, path);
    return child !== '..' && !child.startsWith('..' + sep) && !isAbsolute(child);
  };
  for (const [name, record] of Object.entries(manifest.files)) {
    const file = resolve(root, name);
    if (!inside(file) || !inside(realpathSync(file)))
      throw new Error('Runtime inventory escapes its bundle');
    if (record.symlink !== undefined) {
      if (readlinkSync(file) !== record.symlink)
        throw new Error(`Runtime symlink changed: ${name}`);
    } else if (statSync(file).size !== record.bytes || digest(file) !== record.sha256) {
      throw new Error(`Runtime bytes changed: ${name}`);
    }
  }
  for (const file of inventoryPaths(root)) {
    const name = relative(root, file).split(sep).join('/');
    if (name !== 'manifest.json' && !manifest.files[name])
      throw new Error('Unrecorded runtime file');
  }
  return manifest;
}

export function verifyMediaInventory(root) {
  const provenance = JSON.parse(readFileSync(join(root, 'provenance.json'), 'utf8'));
  if (
    !Array.isArray(provenance.files) ||
    provenance.files.length !== 2 ||
    !/^[0-9a-f]{64}$/.test(provenance.archive?.sha256 ?? '')
  ) {
    throw new Error('Invalid bundled media provenance');
  }
  for (const name of ['ffmpeg', 'ffprobe']) {
    const entry = provenance.files.find((item) => item.file === name);
    if (
      !entry ||
      !/^[0-9a-f]{64}$/.test(entry.sourceSha256 ?? '') ||
      digest(join(root, name)) !== entry.sha256
    ) {
      throw new Error(`Bundled media bytes changed: ${name}`);
    }
  }
  return provenance;
}

/** Refresh only the bytes codesign changed before the app's resource seal. */
export function refreshSignedRuntime(root, signed) {
  const path = join(root, 'manifest.json');
  const manifest = JSON.parse(readFileSync(path, 'utf8'));
  for (const file of signed) {
    const key = relative(root, file).split(sep).join('/');
    if (!manifest.files[key]) throw new Error(`Unrecorded runtime executable: ${key}`);
    manifest.files[key] = {
      ...manifest.files[key],
      bytes: statSync(file).size,
      sha256: digest(file),
    };
  }
  manifest.signedExecutables = signed.map((file) => relative(root, file).split(sep).join('/'));
  writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`);
}

export async function signMasRuntime(opts, packager) {
  const resources = join(opts.app, 'Contents', 'Resources');
  const runtime = join(resources, 'python-runtime');
  const media = join(resources, 'media-tools');
  const directories = [runtime, media];
  for (const directory of directories) {
    const binaries = runtimeSignTargets(directory);
    for (const file of binaries) {
      execFileSync('codesign', codesignArgs(opts, file), { stdio: 'inherit' });
      execFileSync('codesign', ['--verify', '--strict', file], { stdio: 'inherit' });
    }
    if (directory === runtime && existsSync(runtime)) refreshSignedRuntime(runtime, binaries);
    else if (existsSync(join(media, 'provenance.json'))) {
      const path = join(media, 'provenance.json');
      const record = JSON.parse(readFileSync(path, 'utf8'));
      for (const item of record.files) {
        item.sourceSha256 ??= item.sha256;
        item.sha256 = digest(join(media, item.file));
      }
      writeFileSync(path, `${JSON.stringify(record, null, 2)}\n`);
    }
  }
  const previous = [opts.ignore ?? []].flat();
  return signMac(
    {
      ...opts,
      ignore: (file) =>
        directories.some((root) => file.startsWith(root + sep)) ||
        previous.some((rule) => (typeof rule === 'function' ? rule(file) : file.match(rule))),
    },
    packager,
  );
}
