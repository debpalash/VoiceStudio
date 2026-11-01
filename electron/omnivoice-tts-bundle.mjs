// OmniVoice GGUF runtime (`omnivoice-tts`) packaged into the installers (#2780).
//
// Release workflows download the binary that build-omnivoice-tts.yml built for
// the runner's platform into build/omnivoice-tts/, and the installer ships that
// directory as resources/bin/. The backend runs from resources/, so its lookup
// (`<root>/bin/omnivoice-tts-<slug>` in backend/engines/omnivoice_gguf/backend.py)
// finds it there unchanged.
//
// The backend verifies every file against bin/checksums.sha256. Code signing
// rewrites the files it signs, so the manifest is recomputed after signing:
// in afterPack on Windows (the .exe is signed while it is copied), and before
// the bundle seal on macOS (signMac).
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  chmodSync,
  closeSync,
  existsSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

export const OMNIVOICE_TTS_STAGING_DIR = resolve(here, 'build/omnivoice-tts');
export const OMNIVOICE_TTS_RESOURCE_DIR = 'bin';
export const OMNIVOICE_TTS_MANIFEST = 'checksums.sha256';

/**
 * Binary slug the backend's `_platform_slug()` resolves on this installer
 * target, or null when the target has no bundled runtime. Windows on ARM runs
 * the emulated x64 Python runtime, which asks for the x64 binary. Intel Macs
 * cannot run the local backend at all (docs/install/macos.md).
 */
export function omnivoiceTtsSlug(platform, arch) {
  if (platform === 'win32' && (arch === 'x64' || arch === 'arm64')) return 'windows-x86_64';
  if (platform === 'darwin' && arch === 'arm64') return 'darwin-arm64';
  if (platform === 'linux' && arch === 'x64') return 'linux-x86_64';
  return null;
}

export function omnivoiceTtsBinaryName(slug) {
  return `omnivoice-tts-${slug}${slug.startsWith('windows') ? '.exe' : ''}`;
}

function stagedFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).filter((name) => statSync(join(dir, name)).isFile());
}

/** extraResources entry for a staged runtime; empty when nothing was staged. */
export function omnivoiceTtsResources(dir = OMNIVOICE_TTS_STAGING_DIR) {
  const files = stagedFiles(dir);
  return files.some((name) => name.startsWith('omnivoice-tts-')) &&
    files.includes(OMNIVOICE_TTS_MANIFEST)
    ? [{ from: dir, to: OMNIVOICE_TTS_RESOURCE_DIR, filter: ['**/*'] }]
    : [];
}

export function sha256File(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

/** Parse `<hex>  [*]<name>` lines into a name → digest map. */
export function readManifest(path) {
  const entries = new Map();
  for (const raw of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([0-9a-fA-F]{64})\s+\*?(.+)$/);
    if (!match) throw new Error(`Malformed ${OMNIVOICE_TTS_MANIFEST} line: ${line}`);
    entries.set(match[2].trim(), match[1].toLowerCase());
  }
  return entries;
}

/** Recompute every manifest entry from the files as they will ship. */
export function rewriteManifest(binDir) {
  const manifest = join(binDir, OMNIVOICE_TTS_MANIFEST);
  const names = [...readManifest(manifest).keys()].sort();
  const lines = names.map((name) => {
    const file = join(binDir, name);
    if (!existsSync(file))
      throw new Error(`${OMNIVOICE_TTS_MANIFEST} lists a missing file: ${name}`);
    return `${sha256File(file)}  ${name}\n`;
  });
  writeFileSync(manifest, lines.join(''));
}

/** Files the manifest lists whose bytes no longer match it. */
export function manifestMismatches(binDir) {
  const mismatches = [];
  for (const [name, digest] of readManifest(join(binDir, OMNIVOICE_TTS_MANIFEST))) {
    const file = join(binDir, name);
    if (!existsSync(file) || sha256File(file) !== digest) mismatches.push(name);
  }
  return mismatches;
}

function packagedBinDir(context) {
  return join(context.packager.getResourcesDir(context.appOutDir), OMNIVOICE_TTS_RESOURCE_DIR);
}

/**
 * afterPack: restore the execute bits the artifact download drops and refresh
 * the manifest. On Windows electron-builder has already signed the runtime's
 * .exe while copying extraResources (when a certificate is configured), so the
 * manifest must describe the signed bytes. macOS signing happens in signMac.
 */
export function finalizeOmnivoiceTts(context) {
  const binDir = packagedBinDir(context);
  if (!existsSync(join(binDir, OMNIVOICE_TTS_MANIFEST))) return;
  if (context.electronPlatformName !== 'win32') {
    for (const name of stagedFiles(binDir)) {
      if (name !== OMNIVOICE_TTS_MANIFEST) chmodSync(join(binDir, name), 0o755);
    }
  }
  rewriteManifest(binDir);
}

const MACH_O_MAGICS = new Set([
  'feedface',
  'feedfacf',
  'cefaedfe',
  'cffaedfe',
  'cafebabe',
  'bebafeca',
]);

export function isMachO(path) {
  const descriptor = openSync(path, 'r');
  try {
    const header = Buffer.alloc(4);
    return readSync(descriptor, header, 0, 4, 0) === 4 && MACH_O_MAGICS.has(header.toString('hex'));
  } finally {
    closeSync(descriptor);
  }
}

/** The codesign arguments @electron/osx-sign uses for one file. */
export function codesignArgs(opts, file) {
  const options = (opts.optionsForFile && opts.optionsForFile(file)) || {};
  const args = ['--sign', opts.identity, '--force'];
  if (opts.keychain) args.push('--keychain', opts.keychain);
  if (options.requirements) args.push('--requirements', options.requirements);
  args.push(options.timestamp ? `--timestamp=${options.timestamp}` : '--timestamp');
  const flags = [options.signatureFlags ?? []]
    .flat()
    .flatMap((flag) => String(flag).split(','))
    .map((flag) => flag.trim())
    .filter(Boolean);
  if (options.hardenedRuntime) flags.push('runtime');
  if (flags.length) args.push('--options', [...new Set(flags)].join(','));
  if (options.additionalArguments) args.push(...options.additionalArguments);
  if (options.entitlements) args.push('--entitlements', options.entitlements);
  args.push(file);
  return args;
}

/**
 * mac.sign: sign the bundled runtime first with the app's identity and
 * options, refresh its manifest, then run electron-builder's default signing
 * for everything else (skipping the runtime, so its bytes keep matching the
 * manifest the bundle seal now covers).
 */
export async function signMac(opts, packager) {
  const binDir = join(opts.app, 'Contents', 'Resources', OMNIVOICE_TTS_RESOURCE_DIR);
  let ignore = opts.ignore;
  if (opts.identity && existsSync(join(binDir, OMNIVOICE_TTS_MANIFEST))) {
    for (const name of stagedFiles(binDir)) {
      const file = join(binDir, name);
      if (isMachO(file)) execFileSync('codesign', codesignArgs(opts, file), { stdio: 'inherit' });
    }
    rewriteManifest(binDir);
    const previous = [opts.ignore ?? []].flat();
    ignore = (file) =>
      file.startsWith(binDir + sep) ||
      previous.some((rule) => (typeof rule === 'function' ? rule(file) : file.match(rule)));
  }
  return packager.doSign({ ...opts, ignore }, { sign: null }, { name: opts.identity });
}
