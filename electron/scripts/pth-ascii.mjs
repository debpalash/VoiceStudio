import { randomUUID } from 'node:crypto';
import * as nodeFs from 'node:fs';
import * as nodeFsPromises from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Keep a runtime's `.pth` files ASCII so Python can start (#1783).
 *
 * Python 3.11 decodes `.pth` files in the ANSI code page (site.addpackage uses
 * `encoding="locale"`, which ignores PYTHONUTF8). uv's editable install writes
 * the project path into `_editable_impl_omnivoice.pth` as UTF-8, so on a
 * non-UTF-8 Windows code page (cp936/cp932/cp874/...) a non-English username or
 * checkout folder made the interpreter die in `init_import_site` before any
 * VoiceStudio code ran.
 *
 * A non-ASCII path line is rewritten into an equivalent ASCII-only `import`
 * line that appends the same directory with escaped characters; non-ASCII
 * comment lines are dropped. ASCII files are never touched, so this is a no-op
 * everywhere except the affected installs.
 *
 * Shared by the installed app's runtime and the source-checkout scripts
 * (`setup:api`, `dev:api`), which run under plain Node and cannot import TS.
 * `fs` is injectable so tests can simulate Windows sharing violations.
 */
export async function asciiSafePthFiles(venv, fs = nodeFsPromises) {
  const rewritten = [];
  for (const sitePackages of await sitePackagesDirs(venv, fs)) {
    let names;
    try {
      names = await fs.readdir(sitePackages);
    } catch {
      continue;
    }
    for (const name of names.filter(isPth)) {
      const file = join(sitePackages, name);
      const text = healedPthText(await fs.readFile(file).catch(() => null));
      if (text === null) continue;
      if (await replaceFile(file, text, fs)) rewritten.push(file);
    }
  }
  return rewritten;
}

/**
 * Synchronous `asciiSafePthFiles` for callers that must heal right before a
 * synchronous spawn, such as the dev backend's reload and crash restarts.
 */
export function asciiSafePthFilesSync(venv, fs = nodeFs) {
  const rewritten = [];
  for (const sitePackages of sitePackagesDirsSync(venv, fs)) {
    let names;
    try {
      names = fs.readdirSync(sitePackages);
    } catch {
      continue;
    }
    for (const name of names.filter(isPth)) {
      const file = join(sitePackages, name);
      let bytes = null;
      try {
        bytes = fs.readFileSync(file);
      } catch {
        // Unreadable: leave it for Python to report.
      }
      const text = healedPthText(bytes);
      if (text === null) continue;
      if (replaceFileSync(file, text, fs)) rewritten.push(file);
    }
  }
  return rewritten;
}

/**
 * Replace `file` atomically through a sibling temp file: an interrupted heal
 * leaves the original `.pth` intact, and site ignores the temp file because it
 * does not end in .pth. The temp file takes the original's permission bits, so
 * the replacement never exposes the embedded home path more widely.
 *
 * Windows refuses to replace a file while another handle holds it open (an
 * antivirus scan, a concurrent heal), so a refused rename is retried briefly,
 * as graceful-fs does. Returns false when another heal replaced the file first.
 */
async function replaceFile(file, text, fs) {
  const temp = tempPath(file);
  try {
    const mode = (await fs.stat(file)).mode & 0o7777;
    await fs.writeFile(temp, text, { encoding: 'utf8', mode });
    await fs.chmod(temp, mode);
    for (let attempt = 1; ; attempt++) {
      try {
        await fs.rename(temp, file);
        return true;
      } catch (error) {
        if (await healedOrGone(file, fs)) return false;
        if (!retryableRename(error, attempt)) throw error;
        await new Promise((resolve) => setTimeout(resolve, renameBackoffMs(attempt)));
      }
    }
  } finally {
    await fs.rm(temp, { force: true });
  }
}

function replaceFileSync(file, text, fs) {
  const temp = tempPath(file);
  try {
    const mode = fs.statSync(file).mode & 0o7777;
    fs.writeFileSync(temp, text, { encoding: 'utf8', mode });
    fs.chmodSync(temp, mode);
    for (let attempt = 1; ; attempt++) {
      try {
        fs.renameSync(temp, file);
        return true;
      } catch (error) {
        if (healedOrGoneSync(file, fs)) return false;
        if (!retryableRename(error, attempt)) throw error;
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, renameBackoffMs(attempt));
      }
    }
  } finally {
    fs.rmSync(temp, { force: true });
  }
}

const RENAME_ATTEMPTS = 10;

function retryableRename(error, attempt) {
  return ['EPERM', 'EACCES', 'EBUSY'].includes(error?.code) && attempt < RENAME_ATTEMPTS;
}

/** 20, 40, … 180 ms: under a second in total before giving up. */
function renameBackoffMs(attempt) {
  return attempt * 20;
}

/**
 * True once another heal replaced the file or it was removed. A target that
 * cannot be read right now (a transient Windows sharing violation) is not known
 * to be healed, so the caller keeps retrying instead of reporting success.
 */
async function healedOrGone(file, fs) {
  try {
    return healedPthText(await fs.readFile(file)) === null;
  } catch (error) {
    return error?.code === 'ENOENT';
  }
}

function healedOrGoneSync(file, fs) {
  try {
    return healedPthText(fs.readFileSync(file)) === null;
  } catch (error) {
    return error?.code === 'ENOENT';
  }
}

/** Unique per call, so overlapping heals never write or rename each other's file. */
function tempPath(file) {
  return `${file}.${process.pid}.${randomUUID()}.tmp`;
}

function isPth(name) {
  return name.toLowerCase().endsWith('.pth');
}

/** The ASCII rewrite of a `.pth` file's bytes, or null when it needs none. */
function healedPthText(bytes) {
  if (!bytes || bytes.every((byte) => byte < 0x80)) return null;
  try {
    return asciiSafePthText(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    return null; // Not written by uv/pip as UTF-8; leave it alone.
  }
}

/** The ASCII-only equivalent of a `.pth` file's text. */
export function asciiSafePthText(text) {
  return text
    .split(/\r?\n/)
    .flatMap((line) => {
      if (isAscii(line)) return [line];
      const trimmed = line.trim();
      if (trimmed.startsWith('#')) return [];
      if (/^import[ \t]/.test(line)) return [`import builtins; builtins.exec(${pythonLiteral(line)})`];
      // site.addpackage: a path line is relative to sitedir, added once, and
      // only when it exists. `sitedir` is in scope where site execs the line.
      return [
        `import os, sys; p = os.path.abspath(os.path.join(sitedir, ${pythonLiteral(line.trimEnd())})); ` +
          'os.path.exists(p) and p not in sys.path and sys.path.append(p)',
      ];
    })
    .join('\n');
}

function isAscii(value) {
  return [...value].every((char) => char.charCodeAt(0) < 0x80);
}

/** A single-quoted Python string literal using only printable ASCII. */
export function pythonLiteral(value) {
  let out = "'";
  for (const char of value) {
    const code = char.codePointAt(0);
    if (char === '\\') out += '\\\\';
    else if (char === "'") out += "\\'";
    else if (code >= 0x20 && code < 0x7f) out += char;
    else if (code <= 0xff) out += `\\x${code.toString(16).padStart(2, '0')}`;
    else if (code <= 0xffff) out += `\\u${code.toString(16).padStart(4, '0')}`;
    else out += `\\U${code.toString(16).padStart(8, '0')}`;
  }
  return `${out}'`;
}

async function sitePackagesDirs(venv, fs) {
  let entries = [];
  try {
    entries = await fs.readdir(join(venv, 'lib'));
  } catch {
    // Windows layout, or no venv yet.
  }
  return sitePackagesFrom(venv, entries);
}

function sitePackagesDirsSync(venv, fs) {
  let entries = [];
  try {
    entries = fs.readdirSync(join(venv, 'lib'));
  } catch {
    // Windows layout, or no venv yet.
  }
  return sitePackagesFrom(venv, entries);
}

function sitePackagesFrom(venv, libEntries) {
  return [
    join(venv, 'Lib', 'site-packages'),
    ...libEntries
      .filter((entry) => entry.startsWith('python'))
      .map((entry) => join(venv, 'lib', entry, 'site-packages')),
  ];
}
