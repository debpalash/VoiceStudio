export type FindableSegment = {
  id: string;
  text?: string;
  text_original?: string;
};

export type TextMatch = {
  id: string;
  field: 'text' | 'text_original';
  start: number;
  end: number;
};

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function queryPattern(query: string): RegExp | null {
  const needle = query.trim();
  if (!needle) return null;
  return new RegExp(escapeRegExp(needle), 'giu');
}

function collectFieldMatches(
  id: string,
  field: TextMatch['field'],
  value: string | undefined,
  re: RegExp,
): TextMatch[] {
  if (typeof value !== 'string' || !value) return [];
  const matches: TextMatch[] = [];
  re.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(value)) !== null) {
    if (!match[0]) {
      re.lastIndex += 1;
      continue;
    }
    matches.push({
      id,
      field,
      start: match.index,
      end: match.index + match[0].length,
    });
    if (re.lastIndex === match.index) re.lastIndex += 1;
  }
  return matches;
}

export function collectMatches(
  segments: ReadonlyArray<FindableSegment | null | undefined>,
  query: string,
): TextMatch[] {
  const re = queryPattern(query);
  if (!re) return [];
  const matches: TextMatch[] = [];
  for (const segment of segments) {
    if (!segment || segment.id == null) continue;
    const id = String(segment.id);
    matches.push(...collectFieldMatches(id, 'text', segment.text, re));
    if (segment.text_original && segment.text_original !== segment.text) {
      matches.push(...collectFieldMatches(id, 'text_original', segment.text_original, re));
    }
  }
  return matches;
}

export function nextMatchIndex(count: number, current: number, delta: number): number {
  if (!Number.isFinite(count) || count <= 0) return -1;
  const from = Number.isFinite(current) ? current : 0;
  return (((from + delta) % count) + count) % count;
}

export function replaceAllInText(
  text: string,
  query: string,
  replacement: string,
): { text: string; count: number } {
  const re = queryPattern(query);
  if (!re) return { text, count: 0 };
  let count = 0;
  const next = text.replace(re, () => {
    count += 1;
    return replacement;
  });
  return { text: next, count };
}

export function replaceCurrentMatch<T extends FindableSegment>(
  segments: ReadonlyArray<T>,
  match: TextMatch,
  replacement: string,
): T[] {
  if (match.field !== 'text') return segments.slice();
  return segments.map((segment) => {
    if (String(segment.id) !== match.id) return segment;
    const text = typeof segment.text === 'string' ? segment.text : '';
    if (match.start < 0 || match.end > text.length || match.start > match.end) return segment;
    return {
      ...segment,
      text: text.slice(0, match.start) + replacement + text.slice(match.end),
    };
  });
}

export function applyReplaceAll<T extends FindableSegment>(
  segments: ReadonlyArray<T>,
  query: string,
  replacement: string,
): { segments: T[]; count: number } {
  let count = 0;
  const next = segments.map((segment) => {
    const text = typeof segment.text === 'string' ? segment.text : '';
    const result = replaceAllInText(text, query, replacement);
    if (!result.count) return segment;
    count += result.count;
    return { ...segment, text: result.text };
  });
  return { segments: next, count };
}

export function textUpdates<T extends FindableSegment>(
  previous: ReadonlyArray<T>,
  next: ReadonlyArray<T>,
): Array<{ id: string; text: string }> {
  const before = new Map(previous.map((segment) => [String(segment.id), segment.text ?? '']));
  const updates: Array<{ id: string; text: string }> = [];
  for (const segment of next) {
    const id = String(segment.id);
    const text = segment.text ?? '';
    if (before.get(id) !== text) updates.push({ id, text });
  }
  return updates;
}
