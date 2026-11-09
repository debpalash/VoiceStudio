import { readFileSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { collectHostSnapshot, pathRiskFlags } from './host-snapshot';

it('flags launch-hostile Windows paths without exposing them', () => {
  expect(pathRiskFlags('C:\\Users\\Ana\\OneDrive\\Masaüstü\\VoiceStudio', 'win32')).toEqual([
    'onedrive',
    'non-ascii',
  ]);
  expect(pathRiskFlags('C:\\Windows\\System32\\VoiceStudio', 'win32')).toContain('system32');
  expect(pathRiskFlags('\\\\server\\share\\VoiceStudio', 'win32')).toContain('network-share');
  expect(pathRiskFlags('C:\\Program Files\\VoiceStudio', 'win32')).toEqual([
    'spaces',
    'program-files',
  ]);
  expect(pathRiskFlags('/home/ana/voicestudio', 'linux')).toEqual([]);
});

it('reports machine facts and missing runtime files with no backend running', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'host-snapshot-'));
  try {
    const uv = join(dir, 'uv');
    await writeFile(uv, '');
    const snapshot = await collectHostSnapshot({
      platform: 'linux',
      runtimeProject: dir,
      runtimePython: join(dir, '.venv', 'bin', 'python'),
      uv,
      gpu: async () => 'vendor 0x1002',
    });
    expect(snapshot.runtime).toEqual({ pythonPresent: false, uvPresent: true, pathFlags: [] });
    expect(snapshot.gpu).toBe('vendor 0x1002');
    expect(snapshot.ramGb).toBeGreaterThan(0);
    expect(snapshot.diskFreeGb).toBeGreaterThan(0);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

it('survives a hung or throwing GPU probe', async () => {
  const hung = await collectHostSnapshot({
    runtimeProject: null,
    runtimePython: null,
    uv: null,
    gpu: () => new Promise(() => {}),
  });
  const thrown = await collectHostSnapshot({
    runtimeProject: null,
    runtimePython: null,
    uv: null,
    gpu: async () => {
      throw new Error('no gpu');
    },
  });
  expect(hung.gpu).toBeNull();
  expect(thrown.gpu).toBeNull();
  expect(thrown.runtime.pythonPresent).toBeNull();
});

it('reads free disk from the nearest existing ancestor on first run', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'host-snapshot-'));
  try {
    const snapshot = await collectHostSnapshot({
      platform: 'linux',
      runtimeProject: join(dir, 'not', 'created', 'yet'),
      runtimePython: null,
      uv: null,
    });
    expect(snapshot.diskFreeGb).toBeGreaterThan(0);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

it('reports uv missing only when the search completed with no result', async () => {
  const searched = await collectHostSnapshot({
    runtimeProject: null,
    runtimePython: null,
    uv: async () => null,
  });
  const unchecked = await collectHostSnapshot({
    runtimeProject: null,
    runtimePython: null,
    uv: null,
  });
  expect(searched.runtime.uvPresent).toBe(false);
  expect(unchecked.runtime.uvPresent).toBeNull();
});

it('validates the caller before the host snapshot IPC reads anything', () => {
  const source = readFileSync(join(__dirname, 'ipc.ts'), 'utf8');
  const handler = source.split('CHANNELS.backendHostSnapshot,')[1]?.split('ipcMain.handle')[0] ?? '';
  expect(handler).toContain('assertTrustedMainFrame(event, ');
});

it('does not let a hung tool search or stat stall the snapshot', async () => {
  const hungSearch = await collectHostSnapshot({
    runtimeProject: null,
    runtimePython: null,
    uv: () => new Promise(() => {}),
  });
  expect(hungSearch.runtime.uvPresent).toBeNull();
  const found = await collectHostSnapshot({
    runtimeProject: null,
    runtimePython: null,
    uv: async () => '/usr/bin/uv',
  });
  expect(found.runtime.uvPresent).toBe(true);
}, 8000);
