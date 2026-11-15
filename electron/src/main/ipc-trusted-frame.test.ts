// @vitest-environment node
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

const source = readFileSync(resolve(__dirname, 'ipc.ts'), 'utf8');

function handlerBlock(start: string, end: string): string {
  const from = source.indexOf(start);
  expect(from, `missing handler registration: ${start}`).toBeGreaterThan(-1);
  const to = source.indexOf(end, from + start.length);
  expect(to, `missing next registration after ${start}: ${end}`).toBeGreaterThan(-1);
  return source.slice(from, to);
}

it('validates the main frame before revealing a path', () => {
  const block = handlerBlock(
    'ipcMain.handle(CHANNELS.filesRevealPath',
    'ipcMain.handle(CHANNELS.filesOpenExternal',
  );
  expect(block).toContain('assertTrustedMainFrame(event, ');
});

it('validates the main frame before opening an external target', () => {
  const block = handlerBlock(
    'ipcMain.handle(CHANNELS.filesOpenExternal',
    'ipcMain.on(CHANNELS.windowMinimize',
  );
  expect(block).toContain('assertTrustedMainFrame(event, ');
});

it('validates the main frame before reporting window maximize state', () => {
  const block = handlerBlock(
    'ipcMain.handle(CHANNELS.windowIsMaximized',
    'export function wireWindowMaximizeEvents',
  );
  expect(block).toContain('assertTrustedMainFrame(event, ');
});

it('keeps the guarded save-data handler as the positive control', () => {
  const block = handlerBlock(
    'ipcMain.handle(CHANNELS.filesSaveData',
    'ipcMain.handle(CHANNELS.filesRevealPath',
  );
  expect(block).toContain('assertTrustedMainFrame(event, ');
});
