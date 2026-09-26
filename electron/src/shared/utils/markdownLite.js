/**
 * Markdown-lite parsing for release notes (feat/safe-updates).
 *
 * The updater manifest body, GitHub release bodies, and the shipped
 * CHANGELOG.md all use the same house style: `###` headings, `- ` bullets,
 * `**bold leads**`, `` `code` ``, and `(#NNN)` issue refs. This module
 * tokenizes that into plain data — the renderer (`MarkdownLite.jsx`) emits
 * React text nodes only, so arbitrary markdown/HTML can never inject markup
 * (no dangerouslySetInnerHTML anywhere in this path).
 *
 * Pure + framework-free so it is unit-testable in isolation.
 */

/** `[label](url)` → `label`; keeps notes readable without linkifying. */
const LINK_RE = /\[([^\]]*)\]\(([^)\s]*)\)/g;

/**
 * Tokenize one line into inline segments: `{ type: 'text'|'bold'|'code', text }`.
 * - `**bold**` → bold segment
 * - `` `code` `` → code segment
 * - `[label](url)` → plain text of the label (refs stay plain — never links)
 * - everything else (raw HTML included) → inert text
 */
export function inlineSegments(line) {
  const src = String(line ?? '').replace(LINK_RE, '$1');
  const segments = [];
  const push = (type, text) => {
    if (text) segments.push({ type, text });
  };
  let rest = src;
  const TOKEN_RE = /(\*\*([^*]+)\*\*|`([^`]+)`)/;
  while (rest) {
    const m = TOKEN_RE.exec(rest);
    if (!m) {
      push('text', rest);
      break;
    }
    push('text', rest.slice(0, m.index));
    if (m[2] !== undefined) push('bold', m[2]);
    else push('code', m[3]);
    rest = rest.slice(m.index + m[1].length);
  }
  return segments;
}

/**
 * Parse a whole notes string into simple blocks:
 * `{ type: 'heading', level, text }` | `{ type: 'bullet', text }` |
 * `{ type: 'para', text }`.
 * Bullet/paragraph continuation lines (hard-wrapped sources) are joined.
 */
export function parseBlocks(md) {
  const blocks = [];
  let open = null; // last bullet/para still absorbing continuation lines
  for (const raw of String(md ?? '').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      open = null;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*\S)\s*$/.exec(line);
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] });
      open = null;
      continue;
    }
    const bullet = /^[-*]\s+(.*\S)\s*$/.exec(line);
    if (bullet) {
      open = { type: 'bullet', text: bullet[1] };
      blocks.push(open);
      continue;
    }
    if (open) {
      open.text += ` ${line}`;
      continue;
    }
    open = { type: 'para', text: line };
    blocks.push(open);
  }
  return blocks;
}
