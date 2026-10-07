// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';

/**
 * The rehearsal's Intel-Mac leg failed a supervisor test that passes on every
 * other host: setupRuntime() refuses a real darwin/x64 process outright
 * (#2365), so a fixture that drives it never spawned there. Normal PR CI is
 * Linux-only, so this class has to be caught statically: every test that drives
 * the install entry points through the real supervisor must pin its architecture,
 * in its own body or in a top-level beforeEach that runs for every test.
 */
const PIN = /spyOn\(process, 'arch'|defineProperty\(process, 'arch'/;
const DRIVES_INSTALLER = /\.(?:setupRuntime|cleanSetupRuntime)\(/;
const TEST_START = /^[ \t]*(?:it|test)(?:\.\w+)*\(/gm;

/** Titles of tests that drive the installer without an architecture pin. */
function unpinnedInstallerTests(source: string): string[] {
  if (!/new BackendSupervisor\(/.test(source)) return [];
  const fileWide = [...source.matchAll(/^beforeEach\([\s\S]*?^\}\);/gm)].some((m) =>
    PIN.test(m[0]),
  );
  if (fileWide) return [];
  const starts = [...source.matchAll(TEST_START)].map((m) => m.index!);
  return starts.flatMap((start, i) => {
    const body = source.slice(start, starts[i + 1] ?? source.length);
    if (!DRIVES_INSTALLER.test(body) || PIN.test(body)) return [];
    return [body.match(/\(\s*(['"`])(.+?)\1/)?.[2] ?? body.slice(0, 40)];
  });
}

it('pins the host architecture in every test that drives the real runtime installer', () => {
  const offenders = readdirSync(__dirname)
    .filter((name) => name.endsWith('.test.ts') && name !== 'host-independent-tests.test.ts')
    .flatMap((name) =>
      unpinnedInstallerTests(readFileSync(join(__dirname, name), 'utf8')).map(
        (title) => `${name}: ${title}`,
      ),
    );
  expect(offenders).toEqual([]);
});

it('does not let one pinned test hide an unpinned one', () => {
  const pin = "vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');";
  const header = 'const s = new BackendSupervisor();\n';
  const pinned = `it('pinned', async () => {\n  ${pin}\n  ${header}  await s.setupRuntime();\n});\n`;
  const unpinned = `it('unpinned', async () => {\n  ${header}  await s.setupRuntime();\n});\n`;
  expect(unpinnedInstallerTests(pinned + unpinned)).toEqual(['unpinned']);
  expect(unpinnedInstallerTests(pinned)).toEqual([]);
  expect(
    unpinnedInstallerTests(
      `beforeEach(() => {\n  ${pin}\n});\n${pinned.replace(pin, '')}${unpinned}`,
    ),
  ).toEqual([]);
  // A pin buried in a nested hook is not file-wide.
  expect(
    unpinnedInstallerTests(
      `describe('x', () => {\n  beforeEach(() => {\n    ${pin}\n  });\n${unpinned}});\n`,
    ),
  ).toEqual(['unpinned']);
});
