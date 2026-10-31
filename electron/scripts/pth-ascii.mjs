import { readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
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
 */
export async function asciiSafePthFiles(venv) {
  const rewritten = [];
  for (const sitePackages of await sitePackagesDirs(venv)) {
    let names;
    try {
      names = await readdir(sitePackages);
    } catch {
      continue;
    }
    for (const name of names.filter(isPth)) {
      const file = join(sitePackages, name);
      const text = healedPthText(await readFile(file).catch(() => null));
      if (text === null) continue;
      const temp = tempPath(file);
      try {
        await writeFile(temp, text, 'utf8');
        await rename(temp, file);
      } catch (error) {
        await rm(temp, { force: true });
        throw error;
      }
      rewritten.push(file);
    }
  }
  return rewritten;
}

/**
 * Synchronous `asciiSafePthFiles` for callers that must heal right before a
 * synchronous spawn, such as the dev backend's reload and crash restarts.
 */
export function asciiSafePthFilesSync(venv) {
  const rewritten = [];
  for (const sitePackages of sitePackagesDirsSync(venv)) {
    let names;
    try {
      names = readdirSync(sitePackages);
    } catch {
      continue;
    }
    for (const name of names.filter(isPth)) {
      const file = join(sitePackages, name);
      let bytes = null;
      try {
        bytes = readFileSync(file);
      } catch {
        // Unreadable: leave it for Python to report.
      }
      const text = healedPthText(bytes);
      if (text === null) continue;
      const temp = tempPath(file);
      try {
        writeFileSync(temp, text, 'utf8');
        renameSync(temp, file);
      } catch (error) {
        rmSync(temp, { force: true });
        throw error;
      }
      rewritten.push(file);
    }
  }
  return rewritten;
}

/**
 * A sibling path for an atomic replace: an interrupted heal leaves the original
 * `.pth` intact, and site ignores the temp file because it does not end in .pth.
 */
function tempPath(file) {
  return `${file}.${process.pid}.tmp`;
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

async function sitePackagesDirs(venv) {
  let entries = [];
  try {
    entries = await readdir(join(venv, 'lib'));
  } catch {
    // Windows layout, or no venv yet.
  }
  return sitePackagesFrom(venv, entries);
}

function sitePackagesDirsSync(venv) {
  let entries = [];
  try {
    entries = readdirSync(join(venv, 'lib'));
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
