import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const config = (name) =>
  JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../../src-tauri', name), 'utf8'));
const base = config('tauri.conf.json');
const mac = config('tauri.macos.conf.json');

// Tauri uses JSON Merge Patch: platform arrays REPLACE base arrays, they do
// not merge individual window objects. Assert the actual replacement policy.
describe('macOS effective window configuration', () => {
  it('preserves every base window and its behavior except native chrome', () => {
    const windows = mac.app?.windows ?? base.app.windows;
    expect(windows).toHaveLength(base.app.windows.length);
    for (const source of base.app.windows) {
      const effective = windows.find(
        (window) => (window.label ?? 'main') === (source.label ?? 'main'),
      );
      expect(effective).toBeDefined();
      for (const [key, value] of Object.entries(source)) {
        if (key === 'decorations' && (source.label ?? 'main') === 'main') continue;
        expect(effective[key], `${source.label ?? 'main'}.${key}`).toEqual(value);
      }
    }
  });

  it('supplies native overlay controls on macOS when custom controls are hidden', () => {
    const main = mac.app.windows.find((window) => (window.label ?? 'main') === 'main');
    expect(main.decorations ?? true).toBe(true);
    expect(main.titleBarStyle).toBe('Overlay');
  });

  it('preserves the declared macOS version floor', () => {
    expect(mac.bundle.macOS.minimumSystemVersion).toBe('13.3');
  });
});

it('allows the capture widget to hide its own window after recording or idle reconciliation', () => {
  const directory = path.resolve(import.meta.dirname, '../../src-tauri/capabilities');
  const permissions = fs
    .readdirSync(directory)
    .filter((name) => name.endsWith('.json'))
    .map((name) => JSON.parse(fs.readFileSync(path.join(directory, name), 'utf8')))
    .filter((capability) => capability.windows?.includes('widget'))
    .flatMap((capability) => capability.permissions);
  // core:default includes visibility queries, but not the hide command used
  // by CaptureWidget's recording completion and stranded-window recovery.
  expect(permissions).toContain('core:default');
  expect(permissions).toContain('core:window:allow-hide');
});
