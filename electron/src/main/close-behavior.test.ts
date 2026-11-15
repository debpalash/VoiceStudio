import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  decideWindowClose,
  readCloseToTray,
  saveCloseToTray,
  type WindowCloseContext,
} from './close-behavior';

const base: WindowCloseContext = {
  platform: 'win32',
  headlessOwner: false,
  quitting: false,
  quitRequested: false,
  closeToTray: false,
  trayAvailable: true,
};
const decide = (patch: Partial<WindowCloseContext>) => decideWindowClose({ ...base, ...patch });

describe('decideWindowClose', () => {
  it('keeps today\'s behaviour with the setting off', () => {
    expect(decide({ platform: 'win32' })).toBe('quit');
    expect(decide({ platform: 'linux' })).toBe('quit');
    expect(decide({ platform: 'darwin' })).toBe('destroy');
  });

  it('hides to the tray on every platform when the setting is on', () => {
    for (const platform of ['win32', 'linux', 'darwin'] as const)
      expect(decide({ platform, closeToTray: true })).toBe('hide');
  });

  it('falls back to the platform default without a tray icon to return from', () => {
    expect(decide({ closeToTray: true, trayAvailable: false })).toBe('quit');
    expect(decide({ platform: 'darwin', closeToTray: true, trayAvailable: false })).toBe('destroy');
  });

  it('lets a close through once the app is quitting, even with the setting on', () => {
    expect(decide({ closeToTray: true, quitting: true })).toBe('allow');
    expect(decide({ platform: 'darwin', quitting: true })).toBe('allow');
  });

  it('does not hide while an orderly quit or session end is under way', () => {
    for (const platform of ['win32', 'linux', 'darwin'] as const)
      expect(decide({ platform, closeToTray: true, quitRequested: true })).toBe('quit');
  });

  it('leaves a terminal-started app to its idle timer', () => {
    expect(decide({ headlessOwner: true, closeToTray: true })).toBe('destroy');
    expect(decide({ headlessOwner: true, platform: 'linux' })).toBe('destroy');
  });
});

describe('close-to-tray preference', () => {
  let dir = '';
  afterEach(() => rmSync(dir, { recursive: true, force: true }));
  const fresh = () => (dir = mkdtempSync(join(tmpdir(), 'close-behavior-')));

  it('is off by default, for a missing, corrupt or non-boolean file', () => {
    expect(readCloseToTray(fresh())).toBe(false);
    writeFileSync(join(dir, 'window-prefs.json'), '{nope');
    expect(readCloseToTray(dir)).toBe(false);
    writeFileSync(join(dir, 'window-prefs.json'), '{"closeToTray":"yes"}');
    expect(readCloseToTray(dir)).toBe(false);
    writeFileSync(join(dir, 'window-prefs.json'), 'null');
    expect(readCloseToTray(dir)).toBe(false);
  });

  it('round-trips the saved choice', () => {
    saveCloseToTray(fresh(), true);
    expect(readCloseToTray(dir)).toBe(true);
    saveCloseToTray(dir, false);
    expect(readCloseToTray(dir)).toBe(false);
    expect(JSON.parse(readFileSync(join(dir, 'window-prefs.json'), 'utf8'))).toEqual({
      closeToTray: false,
    });
  });

  it('survives an unwritable directory without throwing', () => {
    expect(() => saveCloseToTray(join(fresh(), 'missing', 'deeper'), true)).not.toThrow();
  });
});
