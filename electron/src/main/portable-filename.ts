// Mirrors backend/core/path_security.portable_filename: one rule on every OS so an
// export named after a video title ("How to X: a guide?") cannot hit Windows'
// [Errno 22] Invalid argument. Keep the two in sync (tests cover the same cases).
const INVALID = /[<>:"/\\|?*\u0000-\u001f\u007f]/g;
const RESERVED = new Set([
  'CON',
  'PRN',
  'AUX',
  'NUL',
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);
const encoder = new TextEncoder();

function truncateBytes(text: string, budget: number): string {
  let out = '';
  let used = 0;
  for (const ch of text) {
    const size = encoder.encode(ch).length;
    if (used + size > budget) break;
    out += ch;
    used += size;
  }
  return out;
}

export function portableFilename(value: string, fallback = 'file', maxBytes = 200): string {
  const name = value.replace(INVALID, '_').replace(/^[ .]+|[ .]+$/g, '');
  const dot = name.lastIndexOf('.');
  let stem = name;
  let ext = '';
  if (dot > 0 && name.length - dot - 1 <= 16 && name.length - dot > 1) {
    stem = name.slice(0, dot);
    ext = name.slice(dot);
  }
  const budget = Math.max(2, maxBytes - encoder.encode(ext).length);
  const fit = (text: string) => truncateBytes(text, budget).replace(/[ .]+$/, '');
  stem = fit(stem) || fallback;
  if (!stem.replace(/[_ ]/g, '')) stem = fallback;
  // Check device names AFTER truncation: cutting a long stem can expose "CON".
  if (RESERVED.has(stem.split('.')[0].trimEnd().toUpperCase())) stem = fit(`_${stem}`);
  return stem + ext;
}
