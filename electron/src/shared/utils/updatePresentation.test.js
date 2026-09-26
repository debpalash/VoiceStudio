import { describe, it, expect } from 'vitest';
import { chipPresentation, prepareReleases } from './updatePresentation';

describe('chipPresentation', () => {
  it('idle → version chip', () => {
    expect(chipPresentation('idle', { appVersion: '0.3.0' })).toMatchObject({
      variant: 'idle',
      label: 'v0.3.0',
      icon: 'check',
    });
  });
  it('idle with unknown version → hidden', () => {
    expect(chipPresentation('idle', { appVersion: null })).toBeNull();
  });
  it('checking keeps prior idle chip visible (not hidden)', () => {
    expect(chipPresentation('checking', { appVersion: '0.3.0' })).toMatchObject({
      variant: 'idle',
      label: 'v0.3.0',
    });
  });
  it('available → update label', () => {
    expect(chipPresentation('available', { appVersion: '0.3.0', version: '0.4.0' })).toMatchObject({
      variant: 'available',
      label: '0.4.0',
      icon: 'up',
    });
  });
  it('downloading → percent', () => {
    expect(chipPresentation('downloading', { progress: 42 })).toMatchObject({
      variant: 'downloading',
      label: '42%',
      icon: 'spin',
    });
  });
  it('ready → restart', () => {
    expect(chipPresentation('ready', {})).toMatchObject({ variant: 'ready', icon: 'restart' });
  });
  it('error → failed', () => {
    expect(chipPresentation('error', {})).toMatchObject({ variant: 'error', icon: 'alert' });
  });
});

describe('prepareReleases', () => {
  const raw = [
    { version: '0.4.0', name: 'v0.4.0', date: '2026-06-01', prerelease: true, notes: 'a' },
    { version: '0.3.0', name: 'v0.3.0', date: '2026-05-20', prerelease: false, notes: 'b' },
    { version: '0.2.7', name: 'v0.2.7', date: '2026-05-03', prerelease: false, notes: 'c' },
  ];
  it('stable hides prereleases', () => {
    const out = prepareReleases(raw, 'stable', '0.3.0');
    expect(out.map((r) => r.version)).toEqual(['0.3.0', '0.2.7']);
  });
  it('preview includes prereleases', () => {
    const out = prepareReleases(raw, 'preview', '0.3.0');
    expect(out.map((r) => r.version)).toEqual(['0.4.0', '0.3.0', '0.2.7']);
  });
  it('marks the running version current', () => {
    const out = prepareReleases(raw, 'stable', '0.3.0');
    expect(out.find((r) => r.version === '0.3.0').current).toBe(true);
    expect(out.find((r) => r.version === '0.2.7').current).toBe(false);
  });
  it('tolerates empty/nullish input', () => {
    expect(prepareReleases(null, 'stable', '0.3.0')).toEqual([]);
  });
});
