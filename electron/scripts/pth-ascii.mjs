import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Keep a runtime's `.pth` files ASCII so Python can start (#1783).
 *
 * Python 3.11 decodes `.pth` files in the ANSI code page (site.addpackage uses
 * `encoding="locale"`, which ignores PYTHONUTF8). uv's editable install writes
 * the project path into `_editable_impl_omnivoice.pth` as UTF-8, so on a CJK
 * Windows (cp936/cp932/...) a non-English username made the interpreter die in
 * `init_import_site` before any VoiceStudio code ran.
 *
 * A non-ASCII path line is rewritten into an equivalent ASCII-only `import`
 * line that appends the same directory with escaped characters; non-ASCII
 * comment lines are dropped. ASCII files are never touched, so this is a no-op
 * everywhere except the affected installs.
 *
 * Plain JavaScript so the source-checkout scripts (`setup:api`, `dev:api`) heal
 * the same way as the app; `src/main/pth-ascii.ts` re-exports it.
 *
 * @param {string} venv
 * @returns {Promise<string[]>} the rewritten files
 */
export async function asciiSafePthFiles(venv) {
  /** @type {string[]} */
  const rewritten = [];
  for (const sitePackages of sitePackagesDirs(venv, await listDir(join(venv, 'lib')))) {
    for (const name of pthNames(await listDir(sitePackages))) {
      const file = join(sitePackages, name);
      const text = healedPth(await readFile(file).catch(() => null));
      if (text === null) continue;
      await writeFile(file, text, 'utf8');
      rewritten.push(file);
    }
  }
  return rewritten;
}

/**
 * `asciiSafePthFiles` for synchronous callers (the dev backend supervisor heals
 * right before each launch without delaying it to a later tick).
 *
 * @param {string} venv
 * @returns {string[]} the rewritten files
 */
export function asciiSafePthFilesSync(venv) {
  /** @type {string[]} */
  const rewritten = [];
  for (const sitePackages of sitePackagesDirs(venv, listDirSync(join(venv, 'lib')))) {
    for (const name of pthNames(listDirSync(sitePackages))) {
      const file = join(sitePackages, name);
      let bytes = null;
      try {
        bytes = readFileSync(file);
      } catch {
        // Unreadable: leave it alone.
      }
      const text = healedPth(bytes);
      if (text === null) continue;
      writeFileSync(file, text, 'utf8');
      rewritten.push(file);
    }
  }
  return rewritten;
}

/**
 * The healed text of a `.pth` file, or null when it must stay as it is: already
 * ASCII, unreadable, or not UTF-8 (not written by uv/pip).
 *
 * @param {Uint8Array | null} bytes
 * @returns {string | null}
 */
function healedPth(bytes) {
  if (!bytes || bytes.every((byte) => byte < 0x80)) return null;
  try {
    return asciiSafePthText(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    return null;
  }
}

/** @param {string[]} names */
function pthNames(names) {
  return names.filter((entry) => entry.toLowerCase().endsWith('.pth'));
}

/** @param {string} dir */
async function listDir(dir) {
  return readdir(dir).catch(() => []);
}

/** @param {string} dir */
function listDirSync(dir) {
  try {
    return readdirSync(dir);
  } catch {
    return [];
  }
}

/**
 * The ASCII-only equivalent of a `.pth` file's text.
 * @param {string} text
 */
export function asciiSafePthText(text) {
  return text
    .split(/\r?\n/)
    .flatMap((line) => {
      if (isAscii(line)) return [line];
      const trimmed = line.trim();
      if (trimmed.startsWith('#')) return [];
      if (/^import[ \t]/.test(line))
        return [`import builtins; builtins.exec(${pythonLiteral(line)})`];
      // site.addpackage: a path line is relative to sitedir, added once, and
      // only when it exists. `sitedir` is in scope where site execs the line.
      return [
        `import os, sys; p = os.path.abspath(os.path.join(sitedir, ${pythonLiteral(line.trimEnd())})); ` +
          'os.path.exists(p) and p not in sys.path and sys.path.append(p)',
      ];
    })
    .join('\n');
}

/** @param {string} value */
function isAscii(value) {
  return [...value].every((char) => char.charCodeAt(0) < 0x80);
}

/**
 * A single-quoted Python string literal using only printable ASCII.
 * @param {string} value
 */
export function pythonLiteral(value) {
  let out = "'";
  for (const char of value) {
    const code = /** @type {number} */ (char.codePointAt(0));
    if (char === '\\') out += '\\\\';
    else if (char === "'") out += "\\'";
    else if (code >= 0x20 && code < 0x7f) out += char;
    else if (code <= 0xff) out += `\\x${code.toString(16).padStart(2, '0')}`;
    else if (code <= 0xffff) out += `\\u${code.toString(16).padStart(4, '0')}`;
    else out += `\\U${code.toString(16).padStart(8, '0')}`;
  }
  return `${out}'`;
}

/**
 * Windows keeps packages in `Lib/site-packages`; POSIX in `lib/pythonX.Y/site-packages`.
 *
 * @param {string} venv
 * @param {string[]} libEntries the names inside `<venv>/lib`
 */
function sitePackagesDirs(venv, libEntries) {
  return [
    join(venv, 'Lib', 'site-packages'),
    ...libEntries
      .filter((entry) => entry.startsWith('python'))
      .map((entry) => join(venv, 'lib', entry, 'site-packages')),
  ];
}
