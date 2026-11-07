/**
 * scrub — redact credential-shaped substrings and home directories.
 *
 * The frontend twin of backend/core/scrub.py; must stay at least as strict for
 * the shapes a webview can see (home paths + credential-shaped substrings; env
 * vars aren't reachable from JS).
 *
 * Extracted from utils/bugReport.js (#1177) so transport-layer code can scrub
 * without importing the report builder: bugReport.js statically imports
 * api/client.ts, so client.ts importing bugReport.js back would be a module
 * cycle. This module imports nothing — anyone can depend on it. bugReport.js
 * re-exports `scrubText`/`REDACTED` so every existing import keeps working.
 */

export const REDACTED = '***REDACTED***';

// Thresholds mirror backend/core/scrub.py: long enough that identifiers
// like `hf_hub` or `sk-learn` survive, short enough that real tokens don't.
const TOKEN_PATTERNS = [
  /hf_[A-Za-z0-9]{30,}/g, // HuggingFace
  /github_pat_[A-Za-z0-9_]{20,}/g, // GitHub fine-grained PAT
  /gh[pousr]_[A-Za-z0-9]{30,}/g, // GitHub classic tokens
  /sk-[A-Za-z0-9_-]{20,}/g, // OpenAI-style API keys
  // A backend error can carry a secret from any provider over HTTP into
  // error.message/.stack — the webview has no env-var backstop, so these
  // shapes are its only defense. Mirror backend/core/scrub.py.
  /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}/g, // JWT (Bearer)
  /AIza[0-9A-Za-z_-]{35}/g, // Google API key
  /xox[baprs]-[A-Za-z0-9-]{10,}/g, // Slack token
  /AKIA[0-9A-Z]{16}/g, // AWS access key id
  /bearer\s+[A-Za-z0-9._-]{16,}/gi, // opaque bearer tokens
];

// Secrets in a URL query string — redact the value, keep the param name.
const URL_SECRET_RE =
  /((?:access[_-]?token|api[_-]?key|apikey|auth[_-]?token|token|secret|password|passwd|pwd)=)([^&\s"'#]{6,})/gi;

// A home folder name can contain spaces ("Alice Smith"). Where the path goes on
// past the name, the name is everything up to that separator; the spaced form
// runs first and only matches when a separator follows, so "/home/bob and more"
// still loses just "bob". Names never span a quote, line break or second root.
const SPACED_NAME = (sep) => `[^${sep}\\s"'\\0]+(?: [^${sep}\\s"'\\0]+)+(?=${sep})`;
const NAME = (sep) => `[^${sep}\\s"']+`;
const homePatterns = (name) => [
  // Windows-with-forward-slashes must run BEFORE the bare macOS shape, or
  // `/Users/<name>` inside `C:/Users/<name>` gets eaten first, leaving `C:~`.
  // `i` flag: Windows is case-insensitive and tools emit lowercase `c:\users\`.
  new RegExp(`(?:file:///)?[A-Za-z]:/Users/${name('/')}`, 'gi'), // Windows, forward slashes (webview stacks, file:/// URLs)
  new RegExp(`/Users/${name('/')}`, 'gi'), // macOS
  new RegExp(`/home/${name('/')}`, 'gi'), // Linux
  new RegExp(`[A-Za-z]:\\\\Users\\\\${name('\\\\')}`, 'gi'), // Windows, backslashes
];
const HOME_PATTERNS = [...homePatterns(SPACED_NAME), ...homePatterns(NAME)];

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * The running user's real home directory, where this context can see it (the
 * main process; the sandboxed renderer has no `process`). It is removed first
 * and whole, whatever characters its folder name holds, in both slash styles.
 */
function realHomePatterns() {
  const env = globalThis.process?.env;
  if (!env) return [];
  const homes = [env.HOME, env.USERPROFILE].filter(
    (home) => typeof home === 'string' && home.replace(/[\\/]+$/, '').length > 3,
  );
  return homes.flatMap((home) => {
    const trimmed = home.replace(/[\\/]+$/, '');
    const forms = new Set([trimmed, trimmed.replace(/\\/g, '/')]);
    return [...forms].map((form) => new RegExp(`${escapeRegExp(form)}(?=$|[\\\\/\\s"'])`, 'gi'));
  });
}

/** Redact credential-shaped substrings and home directories. */
export function scrubText(text) {
  if (text == null) return '';
  let s = String(text);
  for (const pat of TOKEN_PATTERNS) s = s.replace(pat, REDACTED);
  s = s.replace(URL_SECRET_RE, (_m, name) => `${name}${REDACTED}`);
  for (const pat of [...realHomePatterns(), ...HOME_PATTERNS]) s = s.replace(pat, '~');
  return s;
}
