import type { ReactNode } from 'react';
import {
  inlineSegments,
  parseBlocks,
  type MarkdownBlock,
} from '@shared/utils/markdownLite';

function InlineMarkdown({ text }: { text: string }) {
  return inlineSegments(text).map((segment, index) => {
    const key = `${segment.type}-${index}`;
    if (segment.type === 'bold')
      return (
        <strong key={key} className="font-semibold text-foreground">
          {segment.text}
        </strong>
      );
    if (segment.type === 'code')
      return (
        <code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.92em] text-foreground">
          {segment.text}
        </code>
      );
    return <span key={key}>{segment.text}</span>;
  });
}

function flushBullets(output: ReactNode[], bullets: MarkdownBlock[]) {
  if (!bullets.length) return;
  output.push(
    <ul key={`list-${output.length}`} className="list-disc space-y-1.5 pl-4 marker:text-primary/70">
      {bullets.map((bullet, index) => (
        <li key={index}>
          <InlineMarkdown text={bullet.text} />
        </li>
      ))}
    </ul>,
  );
  bullets.length = 0;
}

export function MarkdownLite({ text, className = '' }: { text: string; className?: string }) {
  const output: ReactNode[] = [];
  const bullets: MarkdownBlock[] = [];
  for (const block of parseBlocks(text)) {
    if (block.type === 'bullet') {
      bullets.push(block);
      continue;
    }
    flushBullets(output, bullets);
    if (block.type === 'heading')
      output.push(
        <h3 key={`heading-${output.length}`} className="font-semibold text-foreground">
          <InlineMarkdown text={block.text} />
        </h3>,
      );
    else
      output.push(
        <p key={`paragraph-${output.length}`}>
          <InlineMarkdown text={block.text} />
        </p>,
      );
  }
  flushBullets(output, bullets);
  if (!output.length) return null;
  return <div className={`space-y-2 ${className}`.trim()}>{output}</div>;
}
