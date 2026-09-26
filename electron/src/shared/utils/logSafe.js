const DEFAULT_LOG_VALUE_LIMIT = 512;

/** Render untrusted data as one bounded, control-free console value. */
export function logSafe(value, limit = DEFAULT_LOG_VALUE_LIMIT) {
  let raw;
  try {
    raw = value instanceof Error ? `${value.name}: ${value.message}` : String(value);
  } catch {
    raw = '<unprintable>';
  }
  raw = raw.replaceAll('\r', '\\r').replaceAll('\n', '\\n');
  const cap = Math.max(8, Number.isFinite(limit) ? Math.trunc(limit) : DEFAULT_LOG_VALUE_LIMIT);
  let out = '';
  for (const char of raw) {
    const code = char.codePointAt(0);
    let rendered = char;
    if (char === '\t') rendered = '\\t';
    else if (
      (code >= 0 && code <= 0x1f) ||
      (code >= 0x7f && code <= 0x9f) ||
      code === 0x2028 ||
      code === 0x2029 ||
      /\p{Cf}/u.test(char)
    ) {
      rendered =
        code <= 0xff
          ? `\\x${code.toString(16).padStart(2, '0')}`
          : code <= 0xffff
            ? `\\u${code.toString(16).padStart(4, '0')}`
            : `\\u{${code.toString(16)}}`;
    }
    if (out.length + rendered.length > cap - 1) return `${out}…`;
    out += rendered;
  }
  return out;
}
