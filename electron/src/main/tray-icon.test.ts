import { expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const mocks = vi.hoisted(() => {
  const resized = {
    addRepresentation: vi.fn(),
    setTemplateImage: vi.fn(),
    toPNG: vi.fn(() => Buffer.from('retina')),
  };
  const source = { resize: vi.fn(() => resized), toPNG: vi.fn(() => Buffer.from('icon')) };
  return { source, resized, createFromPath: vi.fn(() => source) };
});
vi.mock('electron', () => ({ nativeImage: { createFromPath: mocks.createFromPath } }));
import { createTrayIcon } from './tray-icon';

it('uses the canonical waveform and spark without the app-icon tile', () => {
  const root = resolve(import.meta.dirname, '../../..');
  const logo = readFileSync(resolve(root, 'docs/logo.svg'), 'utf8');
  const normal = readFileSync(resolve(root, 'electron/build/icons/trayTemplate.svg'), 'utf8');
  const recording = readFileSync(
    resolve(root, 'electron/build/icons/trayRecordingTemplate.svg'),
    'utf8',
  );
  const canonicalPaths = [...logo.matchAll(/<path d="([^"]+)"/g)].map((match) => match[1]);

  expect(canonicalPaths).toHaveLength(2);
  for (const path of canonicalPaths) {
    expect(normal).toContain(`d="${path}"`);
    expect(recording).toContain(`d="${path}"`);
  }
  expect(normal).not.toContain('<rect');
  expect(recording).not.toContain('<rect');
  expect(normal).not.toContain('<circle');
  expect(recording).toContain('<circle');
});

it.each([
  ['32x32.png', 'trayTemplate.png'],
  ['tray-recording.png', 'trayRecordingTemplate.png'],
])('uses an 18-point macOS template for %s', (file, template) => {
  mocks.createFromPath.mockClear();
  mocks.source.resize.mockClear();
  mocks.resized.setTemplateImage.mockClear();
  createTrayIcon(file, 'darwin');
  expect(mocks.createFromPath).toHaveBeenCalledWith(template);
  expect(mocks.source.resize).toHaveBeenCalledWith({ width: 18, height: 18, quality: 'best' });
  expect(mocks.source.resize).toHaveBeenCalledWith({ width: 36, height: 36, quality: 'best' });
  expect(mocks.resized.addRepresentation).toHaveBeenCalledWith({
    scaleFactor: 2,
    buffer: Buffer.from('retina'),
  });
  expect(mocks.resized.setTemplateImage).toHaveBeenCalledWith(true);
});

it('rejects an unknown macOS tray state instead of showing the idle glyph', () => {
  expect(() => createTrayIcon('tray-error.png', 'darwin')).toThrow(
    'Unsupported macOS tray icon: tray-error.png',
  );
});

it.each(['win32', 'linux'] as const)('preserves the existing %s tray image', (platform) => {
  mocks.createFromPath.mockClear();
  mocks.source.resize.mockClear();
  expect(createTrayIcon('32x32.png', platform)).toBe(mocks.source);
  expect(mocks.createFromPath).toHaveBeenCalledWith('32x32.png');
  expect(mocks.source.resize).not.toHaveBeenCalled();
});
