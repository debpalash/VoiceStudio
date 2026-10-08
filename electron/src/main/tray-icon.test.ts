import { expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const resized = { addRepresentation: vi.fn(), toPNG: vi.fn(() => Buffer.from('retina')) };
  const source = { resize: vi.fn(() => resized), toPNG: vi.fn(() => Buffer.from('icon')) };
  return { source, resized, createFromPath: vi.fn(() => source) };
});
vi.mock('electron', () => ({ nativeImage: { createFromPath: mocks.createFromPath } }));
import { createTrayIcon } from './tray-icon';

it.each(['32x32.png', 'tray-recording.png'])('sizes macOS %s to 18 logical pixels', (file) => {
  mocks.source.resize.mockClear();
  createTrayIcon(file, 'darwin');
  expect(mocks.source.resize).toHaveBeenCalledWith({ width: 18, height: 18, quality: 'best' });
  expect(mocks.source.resize).toHaveBeenCalledWith({ width: 36, height: 36, quality: 'best' });
  expect(mocks.resized.addRepresentation).toHaveBeenCalledWith({
    scaleFactor: 2,
    buffer: Buffer.from('retina'),
  });
});

it.each(['win32', 'linux'] as const)('preserves the existing %s tray image', (platform) => {
  mocks.source.resize.mockClear();
  expect(createTrayIcon('32x32.png', platform)).toBe(mocks.source);
  expect(mocks.source.resize).not.toHaveBeenCalled();
});
