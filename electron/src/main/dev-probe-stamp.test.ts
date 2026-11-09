// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { devProbeStamped, writeDevProbeStamp } from './dev-probe-stamp';

let root = '';
let python = '';
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'vs-probe-'));
  const bin = join(root, '.venv', 'bin');
  mkdirSync(bin, { recursive: true });
  mkdirSync(join(root, '.venv', 'lib', 'python3.11', 'site-packages'), { recursive: true });
  python = join(bin, 'python');
  for (const file of [python, join(root, 'uv.lock'), join(root, 'pyproject.toml')])
    writeFileSync(file, 'x');
  writeFileSync(join(root, '.venv', 'pyvenv.cfg'), 'home = /x');
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

it('skips the import probe only after a successful probe of the same environment', () => {
  expect(devProbeStamped(root, python)).toBe(false);
  writeDevProbeStamp(root, python);
  expect(devProbeStamped(root, python)).toBe(true);
});

it.each(['uv.lock', 'pyproject.toml'])('re-probes when %s changes', (name) => {
  writeDevProbeStamp(root, python);
  const file = join(root, name);
  writeFileSync(file, 'changed contents');
  utimesSync(file, new Date(), new Date(Date.now() + 5000));
  expect(devProbeStamped(root, python)).toBe(false);
});

it('re-probes when packages are installed into site-packages', () => {
  writeDevProbeStamp(root, python);
  const site = join(root, '.venv', 'lib', 'python3.11', 'site-packages');
  utimesSync(site, new Date(), new Date(Date.now() + 5000));
  expect(devProbeStamped(root, python)).toBe(false);
});
