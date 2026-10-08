import { execFile } from 'node:child_process';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

function readElfUint16(buffer, offset, littleEndian) {
  return littleEndian ? buffer.readUInt16LE(offset) : buffer.readUInt16BE(offset);
}

function readElfUint32(buffer, offset, littleEndian) {
  return littleEndian ? buffer.readUInt32LE(offset) : buffer.readUInt32BE(offset);
}

function writeElfUint32(buffer, value, offset, littleEndian) {
  if (littleEndian) buffer.writeUInt32LE(value, offset);
  else buffer.writeUInt32BE(value, offset);
}

/** Clear an obsolete executable-stack request in CTranslate2's Linux wheel. */
export async function clearCtranslate2ExecutableStack(sitePackages, platform = process.platform) {
  if (platform !== 'linux') return 0;
  const libraryDir = join(sitePackages, 'ctranslate2.libs');
  const names = await readdir(libraryDir).catch(() => []);
  let patched = 0;
  for (const name of names.filter(
    (candidate) => candidate.startsWith('libctranslate2') && candidate.includes('.so'),
  )) {
    const path = join(libraryDir, name);
    const buffer = await readFile(path);
    if (
      buffer.length < 64 ||
      buffer[0] !== 0x7f ||
      buffer[1] !== 0x45 ||
      buffer[2] !== 0x4c ||
      buffer[3] !== 0x46
    )
      continue;
    const elfClass = buffer[4];
    const littleEndian = buffer[5] === 1;
    if ((elfClass !== 1 && elfClass !== 2) || (!littleEndian && buffer[5] !== 2)) continue;
    const programOffset =
      elfClass === 2
        ? Number(littleEndian ? buffer.readBigUInt64LE(32) : buffer.readBigUInt64BE(32))
        : readElfUint32(buffer, 28, littleEndian);
    const entrySize = readElfUint16(buffer, elfClass === 2 ? 54 : 42, littleEndian);
    const entryCount = readElfUint16(buffer, elfClass === 2 ? 56 : 44, littleEndian);
    let changed = false;
    for (let index = 0; index < entryCount; index += 1) {
      const entry = programOffset + index * entrySize;
      if (entry + entrySize > buffer.length) break;
      if (readElfUint32(buffer, entry, littleEndian) !== 0x6474e551) continue;
      const flagsOffset = entry + (elfClass === 2 ? 4 : 24);
      const flags = readElfUint32(buffer, flagsOffset, littleEndian);
      if ((flags & 1) !== 0) {
        writeElfUint32(buffer, flags & ~1, flagsOffset, littleEndian);
        changed = true;
      }
    }
    if (changed) {
      await writeFile(path, buffer);
      patched += 1;
    }
  }
  return patched;
}

/** Apply the same packaged-runtime compatibility repair to source installs. */
export async function repairSourceNativeLibraries(
  project,
  platform = process.platform,
  { env = process.env, run = execFile } = {},
) {
  if (platform !== 'linux') return;
  const environment = resolve(project, env.UV_PROJECT_ENVIRONMENT || '.venv');
  const python = join(environment, 'bin', 'python');
  const sitePackages = await new Promise((resolve, reject) => {
    run(
      python,
      ['-c', "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
      { cwd: project, env, timeout: 30_000, maxBuffer: 64 * 1024 },
      (error, stdout) => (error ? reject(error) : resolve(stdout.trim())),
    );
  });
  if (!sitePackages) throw new Error('Could not locate Python native libraries.');
  await clearCtranslate2ExecutableStack(sitePackages, platform);
}
