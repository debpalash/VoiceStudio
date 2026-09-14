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
    // The WHATWG windows-1252 decoder maps every byte, so this never throws.
    return new TextDecoder('windows-1252').decode(b);
  }
}

export async function readTextFile(file) {
  return decodeTextBytes(new Uint8Array(await file.arrayBuffer()));
}
