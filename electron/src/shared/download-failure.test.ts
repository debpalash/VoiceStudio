import { expect, it } from 'vitest';
import {
  decodeDownloadFailure,
  encodeDownloadFailure,
  MAX_FAILURE_BODY_CHARS,
  stripIpcErrorPrefix,
} from './download-failure';

const failure = { status: 409, statusText: 'Conflict', body: '{"detail":{"code":"x"}}' };

it('round-trips through the message Electron gives the renderer', () => {
  const wrapped = new Error(
    `Error invoking remote method 'files:saveAudio': Error: ${encodeDownloadFailure(failure)}`,
  );
  expect(decodeDownloadFailure(wrapped)).toEqual(failure);
});

it('keeps a readable first line and bounds the body', () => {
  const message = encodeDownloadFailure({
    ...failure,
    body: 'x'.repeat(MAX_FAILURE_BODY_CHARS * 2),
  });
  expect(message.split('\n')[0]).toBe('Could not download the file (HTTP 409)');
  expect(decodeDownloadFailure(message)?.body).toHaveLength(MAX_FAILURE_BODY_CHARS);
});

it('ignores unrelated or malformed errors', () => {
  expect(decodeDownloadFailure(new Error('no space left on device'))).toBeNull();
  expect(decodeDownloadFailure(new Error('x\nVS_DOWNLOAD_FAILURE:{broken'))).toBeNull();
  expect(decodeDownloadFailure(new Error('x\nVS_DOWNLOAD_FAILURE:{"status":"409"}'))).toBeNull();
  expect(decodeDownloadFailure(null)).toBeNull();
});

it('strips the IPC wrapper from plain errors', () => {
  expect(
    stripIpcErrorPrefix(
      "Error invoking remote method 'files:saveData': Error: no space left on device",
    ),
  ).toBe('no space left on device');
  expect(stripIpcErrorPrefix('already clean')).toBe('already clean');
});
