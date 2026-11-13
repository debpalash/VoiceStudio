// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { legacyStorageEnv } from './legacy-storage';
const roots: string[] = [];
const root = () => {
  const path = mkdtempSync(join(tmpdir(), 'vs-legacy-storage-'));
  roots.push(path);
  return path;
};
afterEach(() => { for (const path of roots.splice(0)) rmSync(path, { recursive: true, force: true }); vi.unstubAllEnvs(); });
it.each([false, true])('preserves existing custom/portable storage without rewriting it (%s)', (portable) => {
  const base = root();
  const data = join(base, 'data');
  const models = join(data, 'models');
  mkdirSync(models, { recursive: true });
  const files = ['voices.json', 'projects.json', 'settings.json', 'database.db', 'models/weights.bin'];
  for (const file of files) writeFileSync(join(data, file), `existing ${file}`);
  const config = portable
    ? { install_mode: 'portable', portable_dir: base }
    : { data_dir: data, models_dir: models };
  writeFileSync(join(base, 'config.json'), JSON.stringify(config));
  expect(legacyStorageEnv([base])).toEqual({ OMNIVOICE_DATA_DIR: data, OMNIVOICE_CACHE_DIR: models });
  for (const file of files) expect(readFileSync(join(data, file), 'utf8')).toBe(`existing ${file}`);
  expect(JSON.parse(readFileSync(join(base, 'config.json'), 'utf8'))).toEqual(config);
});
it('skips malformed configs and relative paths without creating storage', () => {
  const first = root(), second = root();
  writeFileSync(join(first, 'config.json'), 'invalid');
  writeFileSync(join(second, 'config.json'), JSON.stringify({ data_dir: '../unsafe', models_dir: 5 }));
  expect(legacyStorageEnv([first, second])).toEqual({});
});
it('continues past a root with no custom paths and supports older camelCase keys', () => {
  const first = root(), second = root();
  writeFileSync(join(first, 'config.json'), '{}');
  writeFileSync(join(second, 'config.json'), JSON.stringify({ dataDir: second }));
  expect(legacyStorageEnv([first, second])).toEqual({ OMNIVOICE_DATA_DIR: second });
});
