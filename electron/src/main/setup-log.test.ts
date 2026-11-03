// @vitest-environment node
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { SetupLog, scrubSetupText } from './setup-log';

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'vs-setup-log-'));
  roots.push(root);
  return root;
}

it('retains timestamped error chains across retries and app restarts without secrets', async () => {
  const path = join(await fixture(), 'setup.log');
  const log = new SetupLog(path);
  log.append('Attempt 1: C:\\Users\\Alice\\runtime', new Date('2026-01-01T00:00:00Z'));
  log.append('Failed to download torch');
  log.append(
    "cannot decrypt peer's message https://alice:password@download.example/torch?signature=private",
  );
  log.append('Attempt 2');
  const exported = await log.read();
  expect(exported).toContain('2026-01-01T00:00:00.000Z Attempt 1: ~\\runtime');
  expect(exported).toContain("cannot decrypt peer's message");
  expect(exported).not.toMatch(/Alice|alice|password|private/);
  expect(await readFile(path, 'utf8')).toEqual(exported);
  expect(await new SetupLog(path).read()).toContain('Attempt 2');
});

it('keeps the initial context and latest failure while bounding a noisy log', async () => {
  const log = new SetupLog(join(await fixture(), 'setup.log'));
  log.append('Original environment');
  for (let i = 0; i < 400; i++) log.append('x'.repeat(4096));
  log.append('Final TLS failure');
  const exported = await log.read();
  expect(exported.length).toBeLessThanOrEqual(256 * 1024);
  expect(exported).toContain('Original environment');
  expect(exported).toContain('Final TLS failure');
  expect(exported).toContain('omitted');
});

it('exports from memory when the log directory cannot be written', async () => {
  const root = await fixture();
  const blocked = join(root, 'not-a-directory');
  await writeFile(blocked, 'occupied');
  const log = new SetupLog(join(blocked, 'setup.log'));
  log.append('Disk unavailable');
  expect(await log.read()).toContain('exporting this session from memory');
  expect(await log.read()).toContain('Disk unavailable');
});

it('redacts home paths, tokens and short proxy passwords before saving', () => {
  const text = scrubSetupText('https://a:b@proxy.example /home/alice/cache hf_' + 'x'.repeat(35));
  expect(text).toContain('https://REDACTED@proxy.example/');
  expect(text).not.toContain('alice');
  expect(text).not.toContain('hf_');
});
