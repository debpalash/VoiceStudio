// Safe markdown-lite renderer for release notes / changelog bullets.
// Everything is emitted as React TEXT nodes (strong/code wrappers only) —
// no dangerouslySetInnerHTML, so notes from any source stay inert.
import { inlineSegments, parseBlocks } from '../utils/markdownLite';

/** Inline run: **bold** / `code` / plain text (links + refs already plain). */
export function InlineMd({ text }) {
  return (
    <>
      {inlineSegments(text).map((seg, i) =>
        seg.type === 'bold' ? (
          <strong key={i}>{seg.text}</strong>
        ) : seg.type === 'code' ? (
          <code key={i}>{seg.text}</code>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}

/** Block renderer for a whole notes string (updater body / release body). */
export default function MarkdownLite({ text, className = '' }) {
  const blocks = parseBlocks(text);
  if (!blocks.length) return null;
  const out = [];
  let bullets = [];
  const flushBullets = () => {
    if (!bullets.length) return;
    out.push(
      <ul key={`ul-${out.length}`} className="md-lite__list">
        {bullets.map((b, i) => (
          <li key={i}>
            <InlineMd text={b.text} />
          </li>
        ))}
      </ul>,
    );
    bullets = [];
  };
  for (const block of blocks) {
    if (block.type === 'bullet') {
      bullets.push(block);
      continue;
    }
    flushBullets();
    if (block.type === 'heading') {
      out.push(
        <div key={`h-${out.length}`} className="md-lite__heading">
          <InlineMd text={block.text} />
        </div>,
      );
    } else {
      out.push(
        <p key={`p-${out.length}`} className="md-lite__para">
          <InlineMd text={block.text} />
        </p>,
      );
    }
  }
  flushBullets();
  return <div className={`md-lite ${className}`.trim()}>{out}</div>;
}
