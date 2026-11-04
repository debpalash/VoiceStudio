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
  labels = 'bug',
  factsSection = [],
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
    // Backend exception class: an explicit errorClass wins, then the 500
    // handler's error_type, then the diagnosis-taxonomy error_class.
    const payload = error?.payload;
    const pickString = (value) => (typeof value === 'string' && value ? value : '');
    const rawClass =
      pickString(error?.errorClass) ||
      pickString(payload?.error_type) ||
      pickString(payload?.error_class);
    const errorClassText = rawClass ? scrubText(rawClass) : '';
    const rawTopic = pickString(payload?.docs_topic) || pickString(payload?.detail?.docs_topic);
    const failureTopicText = rawTopic ? scrubText(rawTopic) : '';
    errorSection.push(
      '## Error',
      '',
      '```',
      msgForBody,
      ...(errorClassText ? [`Backend error class: ${errorClassText}`] : []),
      ...(failureTopicText ? [`Backend failure topic: ${failureTopicText}`] : []),
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
    // Ahead of everything fitEncoded may cut; the logs are what get truncated.
    ...factsSection,
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

  return `${ISSUES_URL}?title=${encodeURIComponent(title)}&labels=${encodeURIComponent(labels)}&body=${encodeURIComponent(body)}`;
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
