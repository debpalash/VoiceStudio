// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bundledPython, bundledRuntimeEnv } from './bundled-runtime';

const roots: string[] = [];
afterEach(() => roots.splice(0).forEach((root) => rmSync(root, { recursive: true, force: true })));

function runtime(python: string) {
  const root = mkdtempSync(join(tmpdir(), 'mas-runtime-'));
  roots.push(root);
  mkdirSync(join(root, 'python-runtime', 'bin'), { recursive: true });
  writeFileSync(join(root, 'python-runtime', 'bin', 'python3.11'), 'python');
  writeFileSync(
    join(root, 'python-runtime', 'manifest.json'),
    JSON.stringify({ schemaVersion: 1, python }),
  );
  return root;
}

it('locates Python relative to the signed resources after an app move', () => {
  const root = runtime('bin/python3.11');
  expect(bundledPython(root)).toBe(join(root, 'python-runtime', 'bin', 'python3.11'));
});

it.each(['../../external-python', '/bin/sh', 'bin/missing-python'])(
  'rejects an invalid bundled interpreter: %s',
  (path) => {
    expect(() => bundledPython(runtime(path))).toThrow();
  },
);

it('keeps models and caches in the sandbox and disables Python user code overlays', () => {
  const env = bundledRuntimeEnv('/signed/resources', '/sandbox/app-data');
  expect(env.VOICESTUDIO_BUNDLED_CODE_ONLY).toBe('1');
  expect(env.PYTHONNOUSERSITE).toBe('1');
  expect(env.PYTHONDONTWRITEBYTECODE).toBe('1');
  expect(env.OMNIVOICE_DATA_DIR).toBe(join('/sandbox/app-data', 'data'));
  expect(env.HF_HUB_CACHE).toBe(join('/sandbox/app-data', 'models'));
  expect(env.XDG_DATA_HOME).toBe(join('/sandbox/app-data', 'data'));
  expect(env.XDG_CONFIG_HOME).toBe(join('/sandbox/app-data', 'config'));
});
