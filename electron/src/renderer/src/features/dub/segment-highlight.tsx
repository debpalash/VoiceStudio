import { queryPattern } from './segment-find';

export function HighlightedText({
  text,
  query,
  className,
  activeStart,
  activeEnd,
  dir,
}: {
  text: string;
  query: string;
  className?: string;
  activeStart?: number;
  activeEnd?: number;
  /** Mixed-script transcripts keep their own direction (e.g. `auto`). */
  dir?: 'auto' | 'ltr' | 'rtl';
}) {
  const re = queryPattern(query);
  if (!re || !text)
    return (
      <span className={className} dir={dir}>
        {text}
      </span>
    );
  const parts: Array<string | { key: number; value: string; active: boolean }> = [];
  let last = 0;
  let key = 0;
  re.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (!match[0]) {
      re.lastIndex += 1;
      continue;
    }
    if (match.index > last) parts.push(text.slice(last, match.index));
    const end = match.index + match[0].length;
    parts.push({
      key: key++,
      value: match[0],
      active: match.index === activeStart && end === activeEnd,
    });
    last = end;
    if (re.lastIndex === match.index) re.lastIndex += 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return (
    <span className={className} dir={dir}>
      {parts.map((part) =>
        typeof part === 'string' ? (
          part
        ) : (
          <mark
            key={part.key}
            className={
              part.active
                ? 'rounded-sm bg-primary/45 px-0.5 text-foreground'
                : 'rounded-sm bg-primary/25 px-0.5 text-foreground'
            }
          >
            {part.value}
          </mark>
        ),
      )}
    </span>
  );
}
