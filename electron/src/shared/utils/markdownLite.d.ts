export type MarkdownInlineSegment = {
  type: 'text' | 'bold' | 'code';
  text: string;
};

export type MarkdownBlock =
  | { type: 'heading'; level: number; text: string }
  | { type: 'bullet'; text: string }
  | { type: 'para'; text: string };

export function inlineSegments(line: unknown): MarkdownInlineSegment[];
export function parseBlocks(markdown: unknown): MarkdownBlock[];
