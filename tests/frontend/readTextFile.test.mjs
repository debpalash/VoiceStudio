import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeTextBytes, readTextFile } from '../../electron/src/shared/utils/readTextFile.js';

const utf32 = [
  ['LE', [0xff, 0xfe, 0, 0, 0x43, 0, 0, 0, 0x61, 0, 0, 0, 0x66, 0, 0, 0,
    0xe9, 0, 0, 0, 0x20, 0, 0, 0, 0, 0xf6, 1, 0]],
  ['BE', [0, 0, 0xfe, 0xff, 0, 0, 0, 0x43, 0, 0, 0, 0x61, 0, 0, 0, 0x66,
    0, 0, 0, 0xe9, 0, 0, 0, 0x20, 0, 1, 0xf6, 0]],
];
for (const [order, encoded] of utf32) {
  test(`UTF-32 ${order} script reads the same accented and supplementary characters`, async () => {
    const bytes = Uint8Array.from(encoded);
    assert.equal(decodeTextBytes(bytes), 'Café 😀');
    assert.equal(await readTextFile(new Blob([bytes])), 'Café 😀');
    // A Uint8Array can be a window into a larger backing buffer.
    const framed = Uint8Array.from([7, ...encoded, 8]);
    assert.equal(decodeTextBytes(framed.subarray(1, -1)), 'Café 😀');
  });
}

test('UTF-32 invalid scalar values and truncated final units are replaced', () => {
  const bytes = Uint8Array.from([
    0xff, 0xfe, 0, 0, 0, 0xd8, 0, 0, 0, 0, 0x11, 0, 0x41,
  ]);
  assert.equal(decodeTextBytes(bytes), '\ufffd\ufffd\ufffd');
});

test('UTF-16 BOMs and Windows-1252 fallback keep their existing meaning', () => {
  assert.equal(decodeTextBytes(Uint8Array.from([0xff, 0xfe, 0x41, 0])), 'A');
  assert.equal(decodeTextBytes(Uint8Array.from([0xfe, 0xff, 0, 0x41])), 'A');
  assert.equal(decodeTextBytes(Uint8Array.from([0x43, 0x61, 0x66, 0xe9])), 'Café');
});
