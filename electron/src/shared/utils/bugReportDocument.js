import { scrubText } from './scrub';
import { REPO_URL } from './contactLinks';
const ISSUES_URL = `${REPO_URL}/issues/new`;
const MAX_STACK_CHARS = 1800;
const MAX_MSG_CHARS = 1200;
const MAX_ENCODED_BODY = 7000;
function fitEncoded(text, maxEncoded) {
  if (encodeURIComponent(text).length <= maxEncoded) return text;
  let lo = 0;
  let hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (encodeURIComponent(text.slice(0, mid)).length <= maxEncoded) lo = mid;
    else hi = mid - 1;
  }
  return `${text.slice(0, lo)}\n… (truncated)`;
}

export function composeBugReportUrl({
  title = '[Bug] ',
  error,
  ctx = '',
  crashSection = [],
  diagnosticSection = [],
  reachabilitySection = [],
  breadcrumbs = '',
} = {}) {
  const errorSection = [];
  if (error) {
    const msg = scrubText(error?.message || String(error));
    // Seed the title with the failure so the issue list stays scannable;
    // the user can still edit it on github.com before submitting.
    if (title === '[Bug] ' && msg) title = `[Bug] ${msg.slice(0, 80)}`;
    // Cap the message in the body too — a large payload (validation dump,
    // HTML/JSON response body) would otherwise inflate the report past the
    // encoded URL ceiling.
    const msgForBody =
      msg.length > MAX_MSG_CHARS ? `${msg.slice(0, MAX_MSG_CHARS)}\n… (truncated)` : msg;
    let stack = error?.stack ? scrubText(error.stack) : '';
    if (stack.length > MAX_STACK_CHARS) stack = `${stack.slice(0, MAX_STACK_CHARS)}\n… (truncated)`;
    // A generic failure message plus a stack of minified bundle frames is the
    // same report every time; the backend class name is what separates one
    // unclassified engine failure from another (#1800). Taken from the error
    // itself, then from a parsed 500 body — the Electron client keeps that on
    // `payload` instead of lifting the field onto the error (lib/api/client.ts).
    const rawClass =
      typeof error?.errorClass === 'string' && error.errorClass
        ? error.errorClass
        : error?.payload?.error_class;
    const klass = typeof rawClass === 'string' && rawClass ? scrubText(rawClass) : '';
    errorSection.push(
      '## Error',
      '',
      '```',
      msgForBody,
      ...(klass ? [`Backend error class: ${klass}`] : []),
      ...(stack && stack !== msgForBody ? [stack] : []),
      '```',
      '',
    );
  }

  // Action names only (see utils/breadcrumbs.js privacy rules) — still
  // scrubbed as belt-and-braces, and the user reviews it all on github.com.
  const crumbs = scrubText(breadcrumbs);
  const crumbSection = crumbs ? ['## Recent actions', '', '```', crumbs, '```', ''] : [];

  let body = [
    '<!-- Click Submit at the bottom of this page to file the issue.',
    '     Review the auto-captured environment info below and add anything',
    '     about what you were doing when the bug happened. -->',
    '',
    '## Describe the bug',
    '',
    '<!-- e.g. "Synthesize failed in Design mode after picking Narrator personality" -->',
    '',
    ...errorSection,
    '## Environment',
    '',
    ctx,
    '',
    ...reachabilitySection,
    ...diagnosticSection,
    ...crashSection,
    ...crumbSection,
    '## What I was doing',
    '',
    '<!-- step-by-step would help us reproduce -->',
    '',
  ].join('\n');
  body = fitEncoded(scrubText(body), MAX_ENCODED_BODY);

  return `${ISSUES_URL}?title=${encodeURIComponent(title)}&labels=${encodeURIComponent('bug')}&body=${encodeURIComponent(body)}`;
}

export function buildIssueSearchUrl(error) {
  const msg = scrubText(error?.message || String(error || ''));
  const terms = msg
    .replace(/[^a-zA-Z\s]/g, ' ') // drop numbers/punctuation — machine-specific
    .split(/\s+/)
    .filter((w) => w.length > 2)
    .slice(0, 6)
    .join(' ');
  const q = `is:issue ${terms}`.trim();
  return `${REPO_URL}/issues?q=${encodeURIComponent(q)}`;
}
