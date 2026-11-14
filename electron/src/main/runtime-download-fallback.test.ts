import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, expect, it, vi } from 'vitest';
import { syncLockedWithMirror } from './runtime-project';
import { AUTO_PYPI_MIRROR, PYPI_FILES_ORIGIN } from './pypi-mirror';
import { failedDownloadSource } from './setup-downloads';

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((p) => rm(p, { recursive: true, force: true })));
});

const artifact = '3e/38/hash/antlr4-python3-runtime-4.9.3.tar.gz';
const source = `[[package]]\nname = "antlr4-python3-runtime"\nversion = "4.9.3"\nsdist = { url = "${PYPI_FILES_ORIGIN}${artifact}", hash = "sha256:fixed" }\n`;
const dnsFailure = Object.assign(new Error('Runtime setup exited with code 1'), {
  stderr: `Failed to fetch:\n  \`${PYPI_FILES_ORIGIN}${artifact}\`\nerror sending request for url (${PYPI_FILES_ORIGIN}${artifact})\ndns error\nNo such host is known. (os error 11001)`,
});

it('Auto retries the actual failed PyPI source archive at the hash-pinned mirror', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voice-runtime-fallback-'));
  roots.push(root);
  await writeFile(join(root, 'uv.lock'), source);
  const locks: string[] = [];
  const sync = vi.fn(async () => {
    locks.push(await readFile(join(root, 'uv.lock'), 'utf8'));
    if (locks.length === 1) throw dnsFailure;
  });
  await syncLockedWithMirror(root, null, sync, new AbortController().signal, {
    allowOriginMirrorFallback: true,
  });
  expect(locks).toHaveLength(2);
  expect(locks[0]).toBe(source);
  expect(locks[1]).toContain(`${AUTO_PYPI_MIRROR}${artifact}`);
  expect(locks[1]).toContain('sha256:fixed');
  expect(await readFile(join(root, 'uv.lock'), 'utf8')).toBe(source);
});

it('keeps an explicit origin choice and restores its lock on failure', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voice-runtime-fallback-'));
  roots.push(root);
  await writeFile(join(root, 'uv.lock'), source);
  const sync = vi.fn().mockRejectedValue(dnsFailure);
  await expect(syncLockedWithMirror(root, null, sync, new AbortController().signal)).rejects.toBe(
    dnsFailure,
  );
  expect(sync).toHaveBeenCalledTimes(1);
  expect(await readFile(join(root, 'uv.lock'), 'utf8')).toBe(source);
});

it.each([
  [
    'Torch DNS failure',
    Object.assign(new Error('dns error'), {
      stderr:
        'error sending request for url (https://download-r2.pytorch.org/whl/torch.whl)\ndns error',
    }),
  ],
  [
    'lookalike host',
    Object.assign(new Error('dns error'), {
      stderr:
        'error sending request for url (https://files.pythonhosted.org.example/packages/a.tar.gz)\ndns error',
    }),
  ],
  [
    'authentication',
    Object.assign(new Error('HTTP 403 Forbidden'), {
      stderr: `Failed to fetch:\n\`${PYPI_FILES_ORIGIN}${artifact}\`\nRequest failed after 5 retries\nHTTP 403 Forbidden`,
    }),
  ],
  ['integrity failure', new Error('Hash mismatch for antlr4-python3-runtime')],
  [
    'certificate failure',
    Object.assign(new Error('error sending request'), {
      stderr: `error sending request for url (${PYPI_FILES_ORIGIN}${artifact})\ninvalid peer certificate: UnknownIssuer`,
    }),
  ],
])('does not select a mirror for %s', async (_label, error) => {
  const root = await mkdtemp(join(tmpdir(), 'voice-runtime-fallback-'));
  roots.push(root);
  await writeFile(join(root, 'uv.lock'), source);
  const sync = vi.fn().mockRejectedValue(error);
  await expect(
    syncLockedWithMirror(root, null, sync, new AbortController().signal, {
      allowOriginMirrorFallback: true,
    }),
  ).rejects.toBe(error);
  expect(sync).toHaveBeenCalledTimes(1);
  expect(await readFile(join(root, 'uv.lock'), 'utf8')).toBe(source);
});

it('restores the lock and stops after the fallback fails', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voice-runtime-fallback-'));
  roots.push(root);
  await writeFile(join(root, 'uv.lock'), source);
  const mirrorError = new Error('mirror unavailable');
  const sync = vi.fn().mockRejectedValueOnce(dnsFailure).mockRejectedValueOnce(mirrorError);
  await expect(
    syncLockedWithMirror(root, null, sync, new AbortController().signal, {
      allowOriginMirrorFallback: true,
    }),
  ).rejects.toBe(mirrorError);
  expect(sync).toHaveBeenCalledTimes(2);
  expect(await readFile(join(root, 'uv.lock'), 'utf8')).toBe(source);
});

it('does not start the fallback after cancellation', async () => {
  const root = await mkdtemp(join(tmpdir(), 'voice-runtime-fallback-'));
  roots.push(root);
  await writeFile(join(root, 'uv.lock'), source);
  const controller = new AbortController();
  const sync = vi.fn(async () => {
    controller.abort();
    throw dnsFailure;
  });
  await expect(
    syncLockedWithMirror(root, null, sync, controller.signal, { allowOriginMirrorFallback: true }),
  ).rejects.toBe(dnsFailure);
  expect(sync).toHaveBeenCalledTimes(1);
  expect(await readFile(join(root, 'uv.lock'), 'utf8')).toBe(source);
});

it('records only the actual failed host and excludes URL credentials and tokens', () => {
  const data = failedDownloadSource(
    'Downloaded https://earlier.example/a.whl\n' +
      'error sending request for url (https://name:private@files.pythonhosted.org/packages/a.tar.gz?token=private)',
  );
  expect(data).toEqual({ host: 'files.pythonhosted.org', pypiArchive: true });
  expect(JSON.stringify(data)).not.toContain('private');
  expect(failedDownloadSource('Downloaded https://earlier.example/a.whl')).toBeNull();
});
