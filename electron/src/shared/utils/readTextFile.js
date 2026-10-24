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
  // Longest BOM first: UTF-32 LE begins with the UTF-16 LE mark.
  // Browser TextDecoder does not implement UTF-32.
  if (b[0] === 0xff && b[1] === 0xfe && b[2] === 0 && b[3] === 0) return decodeUtf32(b, true);
  if (b[0] === 0 && b[1] === 0 && b[2] === 0xfe && b[3] === 0xff) return decodeUtf32(b, false);
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

function decodeUtf32(bytes, littleEndian) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const text = [];
  for (let offset = 4; offset < bytes.length; offset += 4) {
    if (offset + 4 > bytes.length) {
      text.push('\ufffd');
      break;
    }
    const scalar = view.getUint32(offset, littleEndian);
    text.push(String.fromCodePoint(
      scalar <= 0x10ffff && !(scalar >= 0xd800 && scalar <= 0xdfff) ? scalar : 0xfffd,
    ));
  }
  return text.join('');
}
