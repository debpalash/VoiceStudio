// @vitest-environment node
import { expect, it } from 'vitest';
import { portableFilename } from './portable-filename';

it('replaces characters Windows rejects and keeps the extension', () => {
  expect(portableFilename('How to X: a guide?_en.mp4')).toBe('How to X_ a guide__en.mp4');
  expect(portableFilename('a<b>c|d"e*f.srt')).toBe('a_b_c_d_e_f.srt');
});

it('repairs trailing dots/spaces, device names and empty names', () => {
  expect(portableFilename('clip. .')).toBe('clip');
  expect(portableFilename('CON.wav')).toBe('_CON.wav');
  expect(portableFilename('???', 'audio')).toBe('audio');
  expect(portableFilename('', 'audio.wav')).toBe('audio.wav');
});

it('repairs a device name exposed by truncation', () => {
  const name = portableFilename(`CON${' '.repeat(197)}x.wav`);
  expect(name.split('.')[0].toUpperCase()).not.toBe('CON');
  expect(name.endsWith('.wav')).toBe(true);
  expect(new TextEncoder().encode(name).length).toBeLessThanOrEqual(200);
});

it('truncates by UTF-8 bytes without splitting a character or losing the extension', () => {
  const name = portableFilename('\u65e5\u672c\u8a9e'.repeat(60) + '.mp4');
  expect(new TextEncoder().encode(name).length).toBeLessThanOrEqual(200);
  expect(name.endsWith('.mp4')).toBe(true);
  expect(name).not.toContain('�');
});
