// @vitest-environment node
import { chmodSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { inspectSourceCheckout, isVoiceStudioCheckout } from './source-checkout';

let base: string;
beforeEach(() => {
  // A space in the path mirrors real homes such as "C:\Users\Jane Doe".
  base = mkdtempSync(join(tmpdir(), 'vs checkout '));
});
afterEach(() => {
  chmodRecursive(base);
  rmSync(base, { recursive: true, force: true });
});
function chmodRecursive(dir: string) {
  try {
    chmodSync(dir, 0o755);
    for (const entry of readdirSync(dir, { withFileTypes: true }))
      if (entry.isDirectory()) chmodRecursive(join(dir, entry.name));
  } catch {
    /* best effort */
  }
}
function touch(path: string) {
  mkdirSync(join(path, '..'), { recursive: true });
  writeFileSync(path, '');
}
/** What a fresh `git clone` of the repository contains: no venv, no build, no node_modules. */
function freshClone(root: string, { git = true } = {}) {
  if (git) mkdirSync(join(root, '.git', 'objects'), { recursive: true });
  for (const file of ['package.json', 'backend/main.py', 'electron/package.json', 'README.md'])
    touch(join(root, file));
  mkdirSync(join(root, 'electron', 'src', 'main'), { recursive: true });
  return root;
}

it('accepts a fresh clone at its root without leaving files behind', () => {
  const root = freshClone(join(base, 'VoiceStudio'));
  expect(inspectSourceCheckout(root)).toEqual({ ok: true, root });
  expect(readdirSync(root).filter((name) => name.includes('write-check'))).toEqual([]);
  expect(isVoiceStudioCheckout(root)).toBe(true);
});

it('accepts a git worktree, whose .git is a file', () => {
  const root = freshClone(join(base, 'VoiceStudio'), { git: false });
  writeFileSync(join(root, '.git'), 'gitdir: /elsewhere\n');
  expect(inspectSourceCheckout(root)).toEqual({ ok: true, root });
});

it('accepts the folder git clone ran in and resolves the clone inside it', () => {
  const root = freshClone(join(base, 'VoiceStudio'));
  mkdirSync(join(base, 'other-project'));
  expect(inspectSourceCheckout(base)).toEqual({ ok: true, root });
  // Only the exact root counts when restoring a remembered location.
  expect(isVoiceStudioCheckout(base)).toBe(false);
});

it('accepts the clone folder even when the parent sits inside another repository', () => {
  mkdirSync(join(base, '.git'));
  const code = join(base, 'code');
  const root = freshClone(join(code, 'VoiceStudio'));
  expect(inspectSourceCheckout(code)).toEqual({ ok: true, root });
});

it('accepts a folder inside the clone and resolves the clone root', () => {
  const root = freshClone(join(base, 'VoiceStudio'));
  expect(inspectSourceCheckout(join(root, 'electron', 'src', 'main'))).toEqual({ ok: true, root });
});

it('resolves the clone root from a folder nine or more levels deep, past nested repos', () => {
  const root = freshClone(join(base, 'VoiceStudio'));
  const nestedRepo = join(root, 'electron', 'node_modules', '@scope', 'pkg');
  mkdirSync(join(nestedRepo, '.git'), { recursive: true });
  const deep = join(nestedRepo, 'dist', 'esm', 'internal', 'a', 'b', 'c');
  mkdirSync(deep, { recursive: true });
  expect(deep.slice(root.length).split(/[\\/]/).filter(Boolean).length).toBeGreaterThanOrEqual(9);
  expect(inspectSourceCheckout(deep)).toEqual({ ok: true, root });
});

it('names an ambiguous parent with several clones as not a checkout', () => {
  freshClone(join(base, 'a'));
  freshClone(join(base, 'b'));
  expect(inspectSourceCheckout(base)).toEqual({ ok: false, reason: 'not_voicestudio' });
});

it('explains a ZIP download, which has the files but no git history', () => {
  const root = freshClone(join(base, 'VoiceStudio-main'), { git: false });
  expect(inspectSourceCheckout(root)).toEqual({ ok: false, reason: 'not_git' });
});

it('explains an unrelated git repository', () => {
  mkdirSync(join(base, '.git'));
  touch(join(base, 'package.json'));
  expect(inspectSourceCheckout(base)).toEqual({ ok: false, reason: 'not_voicestudio' });
});

it.each([
  ['Windows/Linux install folder', ['resources', 'app.asar']],
  ['macOS bundle contents', ['Contents', 'Resources', 'app.asar']],
])('explains the installed app: %s', (_label, parts) => {
  const install = join(base, 'VoiceStudio');
  touch(join(install, ...parts));
  expect(inspectSourceCheckout(install)).toEqual({ ok: false, reason: 'installed_app' });
});

it.skipIf(process.platform === 'win32' || process.getuid?.() === 0)(
  'explains a clone the app cannot write to',
  () => {
    const root = freshClone(join(base, 'VoiceStudio'));
    chmodSync(root, 0o555);
    expect(inspectSourceCheckout(root)).toEqual({ ok: false, reason: 'read_only' });
  },
);
