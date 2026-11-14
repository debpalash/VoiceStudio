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
const URL_SECRET_NAMES = new Set([
  'access_token',
  'access-token',
  'accesstoken',
  'api_key',
  'api-key',
  'apikey',
  'auth_token',
  'auth-token',
  'authtoken',
  'token',
  'secret',
  'password',
  'passwd',
  'pwd',
  'pin',
]);
const URL_SECRET_NAME = `(?:${[...URL_SECRET_NAMES].join('|')})`;
const URL_SECRET_RE = new RegExp(`(?<![\\w%+.-])(${URL_SECRET_NAME}=)([^&\\s"'#]{6,})`, 'gi');
// Each known ASCII name needs at most three source characters per encoded
// byte. Name probes have this fixed bound, and matched values are scanned once.
const MAX_QUERY_NAME_CHARS = 3 * Math.max(...[...URL_SECRET_NAMES].map((name) => name.length));

function rawQuoteEnd(text, start) {
  const quote = text[start];
  let end = start + 1;
  while (end < text.length) {
    const char = text[end++];
    if (char === '\\' && end < text.length) end++;
    else if (char === quote) break;
  }
  return end;
}

function scrubUrlQueries(text) {
  const parts = [];
  let copied = 0;
  let position = 0;
  while (position < text.length) {
    if (text[position] !== '?' && text[position] !== '&') {
      position++;
      continue;
    }
    const nameStart = position + 1;
    let nameEnd = nameStart;
    const limit = Math.min(text.length, nameStart + MAX_QUERY_NAME_CHARS);
    while (nameEnd < limit && !'=?&#"\''.includes(text[nameEnd]) && !/\s/.test(text[nameEnd]))
      nameEnd++;
    let known = false;
    if (text[nameEnd] === '=') {
      try {
        known = URL_SECRET_NAMES.has(
          decodeURIComponent(text.slice(nameStart, nameEnd).replace(/\+/g, ' ')).toLowerCase(),
        );
      } catch {
        // A malformed encoded name cannot be an accepted credential parameter.
      }
    }
    if (!known) {
      position++;
      continue;
    }
    const valueStart = nameEnd + 1;
    let end = valueStart;
    let quoteStart = end;
    while (quoteStart < text.length && text[quoteStart] === '\\') quoteStart++;
    if (quoteStart > end && (text[quoteStart] === '"' || text[quoteStart] === "'")) {
      // Match the enclosing JSON/log quote depth. More deeply escaped
      // quotes belong to the credential, rather than ending its value.
      const escapeDepth = quoteStart - end;
      const quote = text[quoteStart];
      end = quoteStart + 1;
      while (end < text.length) {
        const slashStart = end;
        while (end < text.length && text[end] === '\\') end++;
        const closes = end - slashStart === escapeDepth && text[end] === quote;
        if (end < text.length) end++;
        if (closes) break;
      }
    } else if (text[end] === '"' || text[end] === "'") {
      end = rawQuoteEnd(text, end);
    }
    // Quoted fragments may be adjacent parts of the same HTTP credential.
    while (end < text.length && !'&#'.includes(text[end]) && !/\s/.test(text[end])) {
      if (text[end] === '"' || text[end] === "'") {
        const after = end + 1;
        // Preserve terminal log quotes; otherwise consume the entire
        // fragment, failing closed if its closing quote is missing.
        if (after === text.length || /\s/.test(text[after]) || '&#'.includes(text[after])) break;
        end = rawQuoteEnd(text, end);
      } else end++;
    }
    parts.push(text.slice(copied, valueStart), REDACTED);
    copied = position = end;
  }
  parts.push(text.slice(copied));
  return parts.join('');
}

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
  // Preserve escaped quote delimiters before the free-text fallback runs.
  s = scrubUrlQueries(s);
  s = s.replace(URL_SECRET_RE, (_m, name) => `${name}${REDACTED}`);
  for (const pat of [...realHomePatterns(), ...HOME_PATTERNS]) s = s.replace(pat, '~');
  return s;
}
