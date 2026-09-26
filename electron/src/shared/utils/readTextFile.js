/**
 * Read a user-picked text file (subtitles, a script) as the text it holds.
 *
 * `File.text()` decodes UTF-8 only and `FileReader.readAsText()` has no
 * Windows-1252 fallback, but Windows tools save these files as UTF-16 with a
 * byte-order mark (Notepad's "Unicode", many subtitle editors) or in the
 * Windows-1252 code page. Same rule as the backend's decode_text_upload: a BOM
 * names the encoding, valid UTF-8 stays UTF-8, anything else is Windows-1252.
 */
export function decodeTextBytes(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  // TextDecoder drops the BOM of the encoding it was built for.
  if (b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf) return new TextDecoder('utf-8').decode(b);
  if (b[0] === 0xff && b[1] === 0xfe) return new TextDecoder('utf-16le').decode(b);
  if (b[0] === 0xfe && b[1] === 0xff) return new TextDecoder('utf-16be').decode(b);
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(b);
  } catch {
    // Decode the C1 range explicitly: some Node/ICU builds treat the
    // windows-1252 label as Latin-1. Undefined CP1252 bytes retain C1 values,
    // matching the browser Encoding Standard and the backend decoder.
    const c1 = [
      0x20ac, 0x81, 0x201a, 0x192, 0x201e, 0x2026, 0x2020, 0x2021, 0x2c6, 0x2030, 0x160, 0x2039,
      0x152, 0x8d, 0x17d, 0x8f, 0x90, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x2dc,
      0x2122, 0x161, 0x203a, 0x153, 0x9d, 0x17e, 0x178,
    ];
    return Array.from(b, (byte) =>
      String.fromCharCode(byte >= 0x80 && byte <= 0x9f ? c1[byte - 0x80] : byte),
    ).join('');
  }
}

export async function readTextFile(file) {
  return decodeTextBytes(new Uint8Array(await file.arrayBuffer()));
}
