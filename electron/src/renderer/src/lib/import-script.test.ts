import { expect, it } from 'vitest';
import { importScript } from './import-script';

const SAMPLE = 'Café crème — it’s late.';

const utf16 = (text: string, littleEndian: boolean): Uint8Array => {
  const out = new Uint8Array(2 + text.length * 2);
  out[0] = littleEndian ? 0xff : 0xfe;
  out[1] = littleEndian ? 0xfe : 0xff;
  const view = new DataView(out.buffer);
  for (let i = 0; i < text.length; i += 1) {
    view.setUint16(2 + i * 2, text.charCodeAt(i), littleEndian);
  }
  return out;
};

const encodings: Record<string, (text: string) => Uint8Array> = {
  'UTF-8': (text) => new TextEncoder().encode(text),
  'UTF-8 with BOM': (text) => new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode(text)]),
  'UTF-16 LE': (text) => utf16(text, true),
  'UTF-16 BE': (text) => utf16(text, false),
  'Windows-1252': (text) => {
    const cp1252: Record<string, number> = { é: 0xe9, è: 0xe8, '—': 0x97, '’': 0x92 };
    return new Uint8Array([...text].map((ch) => cp1252[ch] ?? ch.charCodeAt(0)));
  },
};

function fileFrom(name: string, bytes: Uint8Array): File {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return new File([copy], name);
}

it.each(Object.keys(encodings))('imports a %s clone script as the text it holds', async (name) => {
  const encode = encodings[name];
  if (!encode) throw new Error(`missing encoder for ${name}`);
  await expect(importScript(fileFrom('script.txt', encode(SAMPLE)))).resolves.toBe(SAMPLE);
});

it('imports a Windows-1252 markdown file and normalizes its newlines', async () => {
  const encode = encodings['Windows-1252'];
  if (!encode) throw new Error('missing Windows-1252 encoder');
  await expect(importScript(fileFrom('notes.md', encode(`${SAMPLE}\r\nNext line.`)))).resolves.toBe(
    `${SAMPLE}\nNext line.`,
  );
});
