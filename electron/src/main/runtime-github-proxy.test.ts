import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, expect, it, vi } from 'vitest';
import {
  GITHUB_RELEASE_PROXY,
  githubReleaseProxy,
  mirrorGithubReleaseUrls,
  runtimeDownloadEnv,
  syncLockedWithMirror,
} from './runtime-project';
import { PYPI_FILES_ORIGIN } from './pypi-mirror';

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((p) => rm(p, { recursive: true, force: true })));
});

const wheel =
  'https://github.com/KittenML/KittenTTS/releases/download/0.8.1/kittentts-0.8.1-py3-none-any.whl';
const lock = [
  '[[package]]',
  'name = "kittentts"',
  'version = "0.8.1"',
  `source = { url = "${wheel}" }`,
  `wheels = [{ url = "${wheel}", hash = "sha256:482a" }]`,
  '[[package]]',
  'name = "numpy"',
  `wheels = [{ url = "${PYPI_FILES_ORIGIN}aa/numpy.whl", hash = "sha256:n" }]`,
  'repo = { url = "https://github.com/debpalash/VoiceStudio" }',
  '',
].join('\n');

it('routes only GitHub release assets through the proxy and keeps hashes', () => {
  const mirrored = mirrorGithubReleaseUrls(lock, GITHUB_RELEASE_PROXY);
  expect(mirrored).toContain(`source = { url = "${GITHUB_RELEASE_PROXY}${wheel}" }`);
  expect(mirrored).toContain(`{ url = "${GITHUB_RELEASE_PROXY}${wheel}", hash = "sha256:482a" }`);
  expect(mirrored).toContain(`${PYPI_FILES_ORIGIN}aa/numpy.whl`);
  expect(mirrored).toContain('url = "https://github.com/debpalash/VoiceStudio"');
  expect(mirrorGithubReleaseUrls(mirrored, GITHUB_RELEASE_PROXY)).toBe(mirrored);
});

it('uses the proxy exactly when the managed Python downloads through it', () => {
  expect(githubReleaseProxy(runtimeDownloadEnv('china'))).toBe(GITHUB_RELEASE_PROXY);
  expect(githubReleaseProxy(runtimeDownloadEnv('restricted'))).toBe(GITHUB_RELEASE_PROXY);
  expect(githubReleaseProxy(runtimeDownloadEnv('global'))).toBeNull();
  expect(
    githubReleaseProxy({ UV_PYTHON_INSTALL_MIRROR: 'https://example.test/python' }),
  ).toBeNull();
});

it('keeps the proxy on every attempt and restores the bundled lock (#4502)', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voice-runtime-github-'));
  roots.push(root);
  await writeFile(join(root, 'uv.lock'), lock);
  const locks: string[] = [];
  const failure = new Error('Runtime setup exited with code 1');
  const sync = vi.fn(async () => {
    locks.push(await readFile(join(root, 'uv.lock'), 'utf8'));
    if (locks.length === 1) throw failure;
  });
  await syncLockedWithMirror(
    root,
    'https://mirrors.aliyun.com/pypi/packages/',
    sync,
    new AbortController().signal,
    {},
    new Map(),
    GITHUB_RELEASE_PROXY,
  );
  expect(locks).toHaveLength(2);
  for (const attempt of locks) expect(attempt).toContain(`${GITHUB_RELEASE_PROXY}${wheel}`);
  expect(locks[1]).toContain(`${PYPI_FILES_ORIGIN}aa/numpy.whl`);
  expect(await readFile(join(root, 'uv.lock'), 'utf8')).toBe(lock);
});
