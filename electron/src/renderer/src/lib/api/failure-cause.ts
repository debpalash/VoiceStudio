/** Plain-language cause of a failed generation, inferred from the raw error text. */
export type FailureCause = 'vram' | 'model_missing' | 'busy' | 'timeout' | 'bad_response';

// Two patterns rather than one alternation: only the first is anchored, and a single
// `^a|b` regex reads as a missing anchor (CodeQL js/regex/missing-regexp-anchor).
const HTML_DOCUMENT_START = /^\s*(?:<!doctype\s+html|<html[\s>]|<head[\s>]|<body[\s>])/i;
const HTML_CLOSING_TAG = /<\/(?:html|body|title)>/i;

/** A proxy error page (nginx 504, Cloudflare 524, ...) leaked in place of a message. */
export function looksLikeHtml(text: string): boolean {
  return HTML_DOCUMENT_START.test(text) || HTML_CLOSING_TAG.test(text);
}

const PATTERNS: Array<[FailureCause, RegExp]> = [
  ['vram', /out of (?:video |gpu )?memory|\bOOM\b|gpu_oom|cuda error: out of|insufficient memory/i],
  [
    'model_missing',
    /model[^.\n]{0,40}not (?:been )?(?:downloaded|found|installed)|not downloaded|repository not found|repo[_ ]?id|no such file[^\n]{0,80}\.(?:safetensors|pt|pth|gguf|onnx|bin)|checkpoint[^\n]{0,40}(?:missing|not found)/i,
  ],
  [
    'busy',
    /already (?:running|in progress)|generation_in_progress|engine (?:is )?busy|\b(?:409|429)\b|too many requests|another (?:generation|job)/i,
  ],
  ['timeout', /timed?[ -]?out|timeout|deadline exceeded|\b(?:408|504|524)\b/i],
];

/** Classify raw error text; null when no known cause matches. */
export function classifyFailure(...texts: Array<string | undefined>): FailureCause | null {
  const joined = texts.filter(Boolean).join('\n');
  if (!joined) return null;
  if (looksLikeHtml(joined)) return /\b(?:504|524|408)\b|time/i.test(joined) ? 'timeout' : 'bad_response';
  for (const [cause, pattern] of PATTERNS) if (pattern.test(joined)) return cause;
  return null;
}
