/**
 * bugReport — shared builder for the prefilled GitHub Issues URL.
 *
 * Single source of truth for everything that can leave the machine as a
 * bug report: ReportBugButton (Settings → About), the ErrorBoundary's
 * "Report this bug" action, and error toasts all funnel through
 * `buildBugReportUrl()`. The user always reviews the prefilled form on
 * github.com before anything is submitted — we never POST, never hold a
 * token (CLAUDE.md Capability 2).
 *
 * Everything assembled here is scrubbed with `scrubText` (utils/scrub.js —
 * the frontend twin of backend/core/scrub.py), which must stay at least as
 * strict for the shapes a webview can see (home paths + credential-shaped
 * substrings; env vars aren't reachable from JS).
 */
/* global __APP_VERSION__ -- injected by Vite at build time (vite.config define) */
import i18next from 'i18next';
import { API } from '../api/client';
import { openExternal } from '../api/external';
import { formatBreadcrumbs } from './breadcrumbs';
import { crashAge, describeCrashExit, getLastBackendCrash } from './backendCrash';
import { contactAge, lastBackendContact } from './backendContact';
import { deploymentMode } from './deploymentMode';
import { askConfirm } from './dialog';
import { isOutdated, parseVersionTriple } from './staleBuild';
import { isTauri } from './updater';

/** Canonical project repository — every GitHub link in the app derives from
 * this single constant so a fork/rename can never leave stale links behind. */
export const REPO_URL = 'https://github.com/debpalash/VoiceStudio';

export const ISSUES_URL = `${REPO_URL}/issues/new`;

const APP_VERSION = (typeof __APP_VERSION__ !== 'undefined' && __APP_VERSION__) || 'unknown';

// The scrub primitives live in utils/scrub.js (#1177) so transport-layer code
// (api/client.ts) can scrub without importing this module — bugReport imports
// client for `API`, so the reverse static import would be a cycle. Re-exported
// here because every existing caller (and bugReport.test.js) imports them from
// this module.
export { REDACTED, scrubText } from './scrub';
import { scrubText } from './scrub';

// GitHub truncates very long prefill URLs; keep the encoded result well
// under the ~8k practical ceiling so the user never loses the form.
const MAX_STACK_CHARS = 1800;
const MAX_MSG_CHARS = 1200;
// Crash-marker stderr tail budget (#941) — keep the newest end (the actual
// traceback/abort), the head is uvicorn boot noise.
const MAX_CRASH_TAIL_CHARS = 1200;
// The real ceiling is on the URL-ENCODED body, not the raw string: markdown
// encodes ~1.3–1.6× larger (newlines→%0A, spaces→%20, backticks/#//), so a
// 6000-char raw body can be ~9k encoded and blow past GitHub's limit. Bound
// the encoded length directly.
const MAX_ENCODED_BODY = 7000;

/** Trim `text` so its URL-encoded length is ≤ maxEncoded (binary search on
 *  the raw cut point — exact, and cheap for report-sized strings). */
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

/** Bound every context fetch: a backend that accepts the socket and then
 * stalls must not pin the report button / error-toast / boundary flow on the
 * browser's full network timeout — partial context beats a hung report. */
async function fetchJsonWithTimeout(url, timeoutMs = 2500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: controller.signal });
    return r.ok ? await r.json() : null;
  } finally {
    clearTimeout(timer);
  }
}

// ── Outdated-build deflection ────────────────────────────────────────────
// 6 in 10 sampled "can't reach the backend" reports came from builds that
// were already obsolete when filed and were closed with "please update".
// Before a report is filed, best-effort establish whether the running build
// is behind the latest release, so openBugReport() can nudge toward the
// update and the body can carry a triage-greppable `**Build status:**` line.
//
// Sources per deployment (implementation differs, behavior identical):
//  - Desktop: the Rust updater already checked at launch (channel-aware,
//    offline-tolerant) — read its verdict from the store. No new network
//    path, and the webview CSP has no api.github.com exception to widen.
//  - Browser/dev/Docker: one bounded GET of the latest-release tag, made
//    only when the user has initiated the bug-report flow — a flow whose
//    stated destination is github.com (local-first: nothing new leaves the
//    machine outside that consented action).
const LATEST_RELEASE_API = `${REPO_URL.replace('https://github.com/', 'https://api.github.com/repos/')}/releases/latest`;

let freshnessPromise = null;

/** Test hook — the session cache would otherwise leak between tests. */
export function _resetBuildFreshnessForTests() {
  freshnessPromise = null;
}

async function computeBuildFreshness() {
  // An 'unknown' dev build can't be compared, must never be nudged to
  // "update", and must never claim to be current — stay silent entirely.
  if (!parseVersionTriple(APP_VERSION)) return null;
  if (isTauri()) {
    const { useAppStore } = await import('../store');
    const s = useAppStore.getState();
    // 'available'/'downloading'/'ready' all mean the updater verified a
    // newer signed release exists for the user's channel. 'idle' cannot be
    // told apart from "not checked yet", so it stays silent rather than
    // claiming the build is current.
    if (['available', 'downloading', 'ready'].includes(s.updateStatus)) {
      return { outdated: true, current: APP_VERSION, latest: s.updateVersion || null };
    }
    return null;
  }
  const j = await fetchJsonWithTimeout(LATEST_RELEASE_API);
  const latest = String(j?.tag_name || '').replace(/^v/, '');
  if (!latest) return null;
  return { outdated: isOutdated(APP_VERSION, latest), current: APP_VERSION, latest };
}

/** `{ outdated, current, latest }`, or null when nothing is known. Session-
 * cached on success; a null result is NOT cached so a temporary network
 * blip doesn't disable the deflection for the whole session. Never rejects. */
export async function checkBuildFreshness() {
  if (!freshnessPromise) {
    freshnessPromise = computeBuildFreshness()
      .catch(() => null)
      .then((r) => {
        if (r == null) freshnessPromise = null;
        return r;
      });
  }
  return freshnessPromise;
}

/** Environment lines for the report body. Best-effort — every fetch is
 * optional so a dead backend still yields a usable report. */
async function captureContext(fresh) {
  const lines = [
    `**Version:** \`${APP_VERSION}\``,
    `**Platform:** \`${navigator?.userAgent || 'unknown'}\``,
  ];
  // Greppable by triage: current-version recurrence is the reliability
  // metric, and stale-build reports must be countable separately.
  if (fresh?.outdated) {
    const latest = fresh.latest ? `\`v${fresh.latest}\`` : 'a newer release';
    lines.push(`**Build status:** OUTDATED — ${latest} was already out when this was filed`);
  } else if (fresh?.latest) {
    lines.push(`**Build status:** current at filing time (latest \`v${fresh.latest}\`)`);
  }

  try {
    const j = await fetchJsonWithTimeout(`${API}/system/info`);
    if (j) {
      if (j?.os_version) lines.push(`**OS:** \`${scrubText(j.os_version)}\``);
      else if (j?.platform) lines.push(`**OS:** \`${j.platform}\``);
      if (j?.python) lines.push(`**Python:** \`${j.python}\``);
      if (j?.device) lines.push(`**Compute device:** \`${scrubText(j.device)}\``);
      if (j?.gpu_name) {
        const vram = j?.vram_total_gb ? ` (${j.vram_total_gb} GB VRAM)` : '';
        lines.push(`**GPU:** \`${scrubText(j.gpu_name)}${vram}\``);
      }
      if (j?.cpu_model) lines.push(`**CPU:** \`${scrubText(j.cpu_model)}\``);
      if (j?.ram_total_gb) lines.push(`**RAM:** \`${j.ram_total_gb} GB\``);
      if (j?.disk_free_gb) lines.push(`**Disk free:** \`${j.disk_free_gb} GB\``);
    }
  } catch {
    /* backend down, stalled, or timed out — partial context is fine */
  }

  try {
    const j = await fetchJsonWithTimeout(`${API}/engines`);
    const active = j?.tts?.active;
    if (active) lines.push(`**Active TTS engine:** \`${active}\``);
  } catch {
    /* noop */
  }

  return lines.join('\n');
}

// Python prints a CHAINED traceback root-cause-FIRST: the original error, then
// "The above exception was the direct cause of…", then the wrapper. So keeping
// only the newest end of stderr — right for a plain log — throws away the one
// line that explains the crash and keeps the least informative one.
//
// #1376 is the case in point. The report arrived carrying
//
//     ModuleNotFoundError: Could not import module 'GenerationMixin'.
//     Are this object's requirements defined correctly?
//
// which is transformers' lazy-import wrapper and says nothing about what
// actually failed; the real cause had been cut. Triage had to guess it from
// the shape of the pair. The whole point of auto-capturing stderr is to avoid
// that round-trip, so recover the root-cause line out of the discarded head.
// Python emits these as their own line. Anchored rather than searched as a
// substring: stderr routinely QUOTES tracebacks (a logged exception, a
// subprocess's captured output), and a bare `indexOf` would treat the quoted
// text as a real chain and prepend a "root cause" from the wrong exception.
const CHAIN_MARKER_RE =
  /^(?:The above exception was the direct cause of the following exception|During handling of the above exception, another exception occurred):?$/;

/** The error line that ends the FIRST block of a chained traceback — i.e. the
 *  original cause. Empty string when `text` is not a chained traceback. */
export function rootCauseLine(text) {
  const lines = text.split('\n');
  const marker = lines.findIndex((l) => CHAIN_MARKER_RE.test(l.trim()));
  if (marker <= 0) return '';
  // Walk back past the marker's blank line to the last non-indented line —
  // traceback frames are indented, the exception line is not.
  for (let i = marker - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (line.trim() && !/^\s/.test(line)) return line.trim();
  }
  return '';
}

// Below this much room for actual log output, the root-cause header stops being
// worth its cost — a labelled line with almost nothing under it is harder to
// act on than the raw newest output.
const MIN_TAIL_CHARS = 400;

/** Bound the crash stderr to `max` characters, keeping the newest end AND — for
 *  a chained traceback — the root cause that would otherwise be cut.
 *
 *  The result never exceeds `max`: the prefix is budgeted for BEFORE slicing,
 *  not added on top of a full-size tail. */
export function clampCrashTail(text, max = MAX_CRASH_TAIL_CHARS) {
  if (text.length <= max) return text;

  const PLAIN = '… (truncated)';
  const plainTail = () => `${PLAIN}\n${text.slice(-Math.max(0, max - PLAIN.length - 1))}`;

  const root = rootCauseLine(text);
  if (!root) return plainTail();

  const prefix = `${root}\n… (truncated — chained traceback; the line above is the original cause)\n`;
  const room = max - prefix.length;
  if (room < MIN_TAIL_CHARS) return plainTail();

  const kept = text.slice(-room);
  // Already visible in what we keep — repeating it is noise, and the budget is
  // better spent on more log.
  if (kept.includes(root)) return plainTail();
  return prefix + kept;
}

/** "## Last backend crash" section from the desktop shell's crash marker
 * (#941): exit code/signal + scrubbed stderr tail, so a "backend became
 * unreachable" report arrives WITH the evidence instead of needing a
 * logs-please round-trip. Empty outside Tauri or when nothing ever crashed.
 * The marker's age is stated so a stale (possibly unrelated) crash can't
 * masquerade as fresh evidence. */
async function captureCrashSection() {
  let marker = null;
  try {
    marker = await getLastBackendCrash();
  } catch {
    /* shell forensics unavailable */
  }
  if (!marker) return [];
  const tail = clampCrashTail(scrubText(marker.last_stderr || '').trim());
  return [
    '## Last backend crash (auto-captured — may predate this bug)',
    '',
    `**When:** ${new Date(marker.ts * 1000).toISOString()} (${crashAge(marker)} ago)`,
    `**Exit:** \`${describeCrashExit(marker)}\``,
    `**Uptime before crash:** ${marker.uptime_s} s`,
    `**Backend version:** \`${marker.backend_version}\``,
    '',
    '```',
    // An empty code block reads as "there was nothing to report" and produces
    // an issue nobody can answer (#1375). Say what is missing and where the
    // reporter can get it, so the report asks for the right thing up front.
    tail ||
      '(no output was captured for this crash — please paste the last ~100 ' +
        'lines of Settings → Logs → Backend here)',
    '```',
    '',
  ];
}

/** "## Backend reachability" section (#1164): which deployment this is, and
 * whether/when the backend last answered — the two facts that split every
 * "can't reach the backend" report into diagnosable halves (crashed
 * mid-session vs never started). When the report is built from a transport
 * ApiError, its structured detail (mode at failure time, first failure,
 * retry attempts) rides along too. All values are mode ids, timestamps, and
 * counts — nothing user-generated — but scrubbed anyway as belt-and-braces. */
function captureReachabilitySection(error) {
  const lines = ['## Backend reachability', ''];
  try {
    lines.push(`**Deployment mode:** \`${deploymentMode()}\``);
    const last = lastBackendContact();
    lines.push(
      last != null
        ? `**Last backend response:** ${contactAge(last)} before this report`
        : '**Last backend response:** none this session — it may never have started',
    );
    const d = error?.detail;
    if (d && typeof d === 'object' && !Array.isArray(d)) {
      if (typeof d.firstFailureTs === 'number' && d.firstFailureTs > 0) {
        lines.push(`**First failure:** ${new Date(d.firstFailureTs).toISOString()}`);
      }
      if (typeof d.attempts === 'number') {
        lines.push(`**Attempts before giving up:** ${d.attempts}`);
      }
      if (typeof d.mode === 'string' && d.mode) {
        lines.push(`**Mode at failure time:** \`${scrubText(d.mode)}\``);
      }
      if (typeof d.transport === 'string' && d.transport) {
        lines.push(`**Transport error:** \`${scrubText(d.transport).slice(0, 200)}\``);
      }
    }
  } catch {
    /* reachability context is best-effort — never block the report */
  }
  lines.push('');
  return lines;
}

/**
 * Build the prefilled GitHub Issues URL.
 *
 * @param {object} [opts]
 * @param {string} [opts.title]  Issue title prefill (defaults to '[Bug] ').
 * @param {Error|string} [opts.error]  Error to embed — message + stack are
 *   scrubbed and truncated into an "## Error" section so the report opens
 *   with the actual failure attached.
 */
export async function buildBugReportUrl({ title = '[Bug] ', error } = {}) {
  const fresh = await checkBuildFreshness();
  const ctx = await captureContext(fresh);
  // getLastBackendCrash inside captureCrashSection covers every deployment:
  // the desktop shell's marker, or (browser/dev/Docker) the backend's
  // run-sentinel record via its HTTP fallback — usually unfetchable while
  // the backend is still down, which is why the reachability section below
  // reports the CACHED last-contact data regardless.
  const crashSection = await captureCrashSection();
  const reachabilitySection = captureReachabilitySection(error);

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
    // A generic failure message plus a stack of minified bundle frames is
    // the same report every time; the backend class name is what separates
    // one unclassified engine failure from another (#1800).
    const klass = typeof error?.errorClass === 'string' ? scrubText(error.errorClass) : '';
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
  const crumbs = scrubText(formatBreadcrumbs());
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
    ...crashSection,
    ...crumbSection,
    '## What I was doing',
    '',
    '<!-- step-by-step would help us reproduce -->',
    '',
  ].join('\n');
  body = fitEncoded(body, MAX_ENCODED_BODY);

  return `${ISSUES_URL}?title=${encodeURIComponent(title)}&labels=${encodeURIComponent('bug')}&body=${encodeURIComponent(body)}`;
}

/**
 * The one entry point every "Report bug" affordance goes through
 * (ReportBugButton, ErrorBoundary, error toasts, crash/start-failure
 * notices): gate on build freshness, then open the prefilled issue.
 *
 * On an outdated build the user is offered the latest release first —
 * with a file-anyway escape hatch, because "old build" is a triage
 * heuristic, not proof the bug is fixed. Freshness is best-effort: when
 * nothing is known the report opens exactly as before.
 *
 * Throws like the old openExternal(buildBugReportUrl()) composition did —
 * call sites keep their own failure UX (toast, console warning).
 */
export async function openBugReport({ title, error } = {}) {
  const fresh = await checkBuildFreshness();
  if (fresh?.outdated) {
    const latest = fresh.latest ? `v${fresh.latest}` : '';
    const viewUpdate = await askConfirm(
      i18next.t('reportBug.staleMessage', { current: `v${fresh.current}`, latest }),
      i18next.t('reportBug.staleTitle'),
      {
        okLabel: i18next.t('reportBug.staleView'),
        cancelLabel: i18next.t('reportBug.staleFileAnyway'),
      },
    );
    if (viewUpdate) {
      await openExternal(`${REPO_URL}/releases/latest`);
      return;
    }
  }
  await openExternal(await buildBugReportUrl({ title, error }));
}

/**
 * GitHub issue-search URL for "has someone already hit this?" — opened in
 * the user's browser before they file a duplicate. Search terms come from
 * the scrubbed error message with noise (numbers, paths, quotes) stripped
 * so the query matches across machines.
 */
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
