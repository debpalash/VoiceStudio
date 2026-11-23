import assert from 'node:assert/strict';
import test from 'node:test';

import { decodeTextBytes } from '../../electron/src/shared/utils/readTextFile.js';

for (const littleEndian of [true, false]) {
  test(`imports UTF-32 ${littleEndian ? 'LE' : 'BE'} text with a BOM`, () => {
    const bytes = new Uint8Array(4 + 4 * 4 + 1);
    const view = new DataView(bytes.buffer);
    for (const [index, codePoint] of [0xfeff, 0x41, 0x1f3b5, 0xd800, 0x110000].entries()) {
      view.setUint32(index * 4, codePoint, littleEndian);
    }
    assert.equal(decodeTextBytes(bytes), 'A\u{1f3b5}\ufffd\ufffd\ufffd');
  });
}
