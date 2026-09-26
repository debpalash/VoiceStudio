// Text encoded the ways Windows tools save a subtitle or script file, for
// tests of the code that reads such a file back.

export const SAMPLE = 'Café crème — it’s late.';

// The non-ASCII characters SAMPLE uses, at their Windows-1252 byte values.
const CP1252 = { é: 0xe9, è: 0xe8, '—': 0x97, '’': 0x92 };

const utf16 = (text, littleEndian) => {
  const bytes = [];
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    const pair = [code & 0xff, code >> 8];
    bytes.push(...(littleEndian ? pair : pair.reverse()));
  }
  return bytes;
};

export const ENCODED = {
  'UTF-8': (text) => new TextEncoder().encode(text),
  'UTF-8 with BOM': (text) => new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode(text)]),
  'UTF-16 LE': (text) => new Uint8Array([0xff, 0xfe, ...utf16(text, true)]),
  'UTF-16 BE': (text) => new Uint8Array([0xfe, 0xff, ...utf16(text, false)]),
  'Windows-1252': (text) => new Uint8Array([...text].map((ch) => CP1252[ch] ?? ch.charCodeAt(0))),
};
