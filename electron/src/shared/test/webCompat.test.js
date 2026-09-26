/**
 * #1245: the app was dead on arrival on macOS 12 (Monterey).
 *
 * The reporter's whole session was one line — `view:launchpad` — and
 * "Last backend response: none this session — it may never have started".
 * The stack is a React render throwing:
 *
 *     AbortSignal.timeout is not a function.
 *     (In 'AbortSignal.timeout(2e3)', 'AbortSignal.timeout' is undefined)
 *
 * `useRealtimeEvents` polls backend health with `AbortSignal.timeout(2000)`
 * on mount. That method arrived in Safari 16.0; `tauri.conf.json` declares
 * `minimumSystemVersion: "12.0"` and `docs/install/macos.md` promises macOS 12,
 * which ships WKWebView **15.6**. So this was not an exotic environment — it
 * was the floor we advertise.
 *
 * Two halves are pinned:
 *  1. the polyfill behaves like the real thing (aborts, and with TimeoutError);
 *  2. no app module reaches for a post-floor API that nothing fills in — the
 *     whole class, not just the one method that got reported.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { installAbortSignalTimeout } from '../utils/webCompat.js';

const SRC = path.resolve(__dirname, '..');

describe('AbortSignal.timeout polyfill', () => {
  let original;

  beforeEach(() => {
    vi.useFakeTimers();
    original = AbortSignal.timeout;
  });

  afterEach(() => {
    vi.useRealTimers();
    AbortSignal.timeout = original;
  });

  it('fills the method in when the WebView lacks it', () => {
    // Reproduce Monterey: the method is simply absent.
    delete AbortSignal.timeout;
    expect(AbortSignal.timeout).toBeUndefined();

    installAbortSignalTimeout();
    expect(typeof AbortSignal.timeout).toBe('function');
  });

  it('aborts after the delay, with a TimeoutError reason', () => {
    delete AbortSignal.timeout;
    installAbortSignalTimeout();

    const signal = AbortSignal.timeout(2000);
    expect(signal.aborted).toBe(false);

    vi.advanceTimersByTime(1999);
    expect(signal.aborted).toBe(false);

    vi.advanceTimersByTime(1);
    expect(signal.aborted).toBe(true);
    // `reason` is how a caller tells a timeout apart from a user cancel.
    expect(signal.reason?.name).toBe('TimeoutError');
  });

  it('does not clobber a native implementation', () => {
    const native = vi.fn(() => new AbortController().signal);
    AbortSignal.timeout = native;

    installAbortSignalTimeout();

    expect(AbortSignal.timeout).toBe(native);
  });

  it('is installed before the app boots', () => {
    // The gap fill is worthless if it loads after the chunk that throws.
    const main = fs.readFileSync(path.join(SRC, 'main.jsx'), 'utf8');
    const compat = main.indexOf('utils/webCompat');
    const app = main.indexOf('main-app.jsx');
    expect(compat).toBeGreaterThan(-1);
    expect(app).toBeGreaterThan(-1);
    expect(compat).toBeLessThan(app);
  });
});

/**
 * The recurrence guard. `AbortSignal.timeout` was not special — it was
 * whichever post-floor API we happened to reach for first. Each entry is an
 * API that does NOT exist in Safari 15.6 (the WebView shipped with the macOS
 * version `tauri.conf.json` declares) and that `webCompat.js` does not fill
 * in, so using one would break Monterey exactly the way #1245 did.
 *
 * To use one of these: either fill it in inside `webCompat.js` and delete the
 * entry, or guard the call site with a runtime check and a fallback.
 *
 * WHAT THIS DOES NOT COVER — deliberately stated so it is not over-trusted:
 *
 *  - **Syntax.** A post-floor *grammar* feature (RegExp lookbehind, Safari
 *    16.4) is a parse-time SyntaxError that kills its whole chunk before a
 *    line runs, and no polyfill can help. Text patterns here cannot see that.
 *  - **Dependencies.** Only `electron/src/shared` is scanned. A bundled library using
 *    a post-floor API or grammar is invisible here and ships anyway.
 *  - **CSS.** Tailwind v4's own floor is Safari 16.4, and `index.css` uses
 *    `color-mix()` (16.2) throughout, so the *rendering* floor is already
 *    above macOS 12 regardless of what JavaScript does. See #1268.
 *  - **Computed access.** `AbortSignal["timeout"]` or a destructured
 *    reference evades every pattern below.
 *
 * This guard closes the class it can see — first-party source. The rest is
 * tracked in #1268 rather than pretended away.
 */
const POST_FLOOR_APIS = [
  // Safari 18.0
  { pattern: /\bURL\s*\.\s*parse\s*\(/, name: 'URL.parse', since: 'Safari 18.0' },
  // Safari 17.4
  { pattern: /\bAbortSignal\s*\.\s*any\b/, name: 'AbortSignal.any', since: 'Safari 17.4' },
  { pattern: /\bObject\s*\.\s*groupBy\b/, name: 'Object.groupBy', since: 'Safari 17.4' },
  { pattern: /\bMap\s*\.\s*groupBy\b/, name: 'Map.groupBy', since: 'Safari 17.4' },
  {
    pattern: /\bPromise\s*\.\s*withResolvers\b/,
    name: 'Promise.withResolvers',
    since: 'Safari 17.4',
  },
  { pattern: /\.checkVisibility\s*\(/, name: 'Element#checkVisibility', since: 'Safari 17.4' },
  // Safari 17.0
  { pattern: /\bURL\s*\.\s*canParse\b/, name: 'URL.canParse', since: 'Safari 17.0' },
  // Safari 16.4
  { pattern: /\.isWellFormed\s*\(/, name: 'String#isWellFormed', since: 'Safari 16.4' },
  { pattern: /\.toWellFormed\s*\(/, name: 'String#toWellFormed', since: 'Safari 16.4' },
  { pattern: /\bArray\s*\.\s*fromAsync\b/, name: 'Array.fromAsync', since: 'Safari 16.4' },
  // Safari 16.0 — the change-by-copy family. `with` is the one most likely to
  // be reached for in React state code (`arr.with(i, v)`), and it was missing
  // from this list until review caught it.
  { pattern: /\.toSorted\s*\(/, name: 'Array#toSorted', since: 'Safari 16.0' },
  { pattern: /\.toReversed\s*\(/, name: 'Array#toReversed', since: 'Safari 16.0' },
  { pattern: /\.toSpliced\s*\(/, name: 'Array#toSpliced', since: 'Safari 16.0' },
  // `arr.with(i, v)` is the idiomatic immutable-state form in React code, so
  // it is the sibling most likely to be reached for. It was missing from this
  // list until review caught it.
  { pattern: /\.with\s*\(/, name: 'Array#with', since: 'Safari 16.0' },
  { pattern: /\bAbortSignal\s*\.\s*timeout\b/, name: 'AbortSignal.timeout', since: 'Safari 16.0' },
];

/** Entries `webCompat.js` fills in, so app code may use them freely. */
const POLYFILLED = new Set(['AbortSignal.timeout']);

const walk = (dir, out = []) => {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'test' || entry.name === 'node_modules') continue;
      walk(full, out);
    } else if (/\.(js|jsx|ts|tsx)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
};

describe('no app code depends on an API newer than the macOS floor', () => {
  it('is written against the macOS version tauri.conf.json declares', () => {
    const conf = JSON.parse(
      fs.readFileSync(path.resolve(SRC, '..', 'src-tauri', 'tauri.conf.json'), 'utf8'),
    );
    // The list above is derived from the WebView that ships with this macOS
    // version (13.3 → Safari/WKWebView 16.4). If the floor moves, re-derive it
    // — the test is only as correct as the version it is written against.
    //
    // #1268 closed the gap this assertion used to document. The floor was 12.0
    // while the frontend stack required Safari 16.4 in three independent
    // places: Vite's default build target (`baseline-widely-available` =
    // safari16.4), Tailwind v4's own documented floor, and `@property` in its
    // generated utilities — plus a RegExp lookbehind in a bundled dependency
    // that is a PARSE-time SyntaxError no polyfill can reach. We declare 13.3
    // now because that is what we actually deliver.
    expect(conf.bundle?.macOS?.minimumSystemVersion).toBe('13.3');

    // …and in the macOS-specific overlay, which is what actually ships.
    // Tauri merges tauri.macos.conf.json OVER the base config for a macOS
    // build, so the base value alone decides nothing: this file carried 12.0
    // and would have silently kept shipping a Monterey-installable bundle
    // while the base config and every doc said 13.3 (Greptile P1). A test that
    // reads only the base config validates the wrong file.
    const macConf = JSON.parse(
      fs.readFileSync(path.resolve(SRC, '..', 'src-tauri', 'tauri.macos.conf.json'), 'utf8'),
    );
    expect(macConf.bundle?.macOS?.minimumSystemVersion).toBe('13.3');
  });

  it('uses no unfilled post-Safari-15.6 API', () => {
    const offenders = [];
    for (const file of walk(SRC)) {
      if (file.endsWith(path.join('utils', 'webCompat.js'))) continue;
      const src = fs.readFileSync(file, 'utf8');
      for (const api of POST_FLOOR_APIS) {
        if (POLYFILLED.has(api.name)) continue;
        if (api.pattern.test(src)) {
          offenders.push(`${path.relative(SRC, file)} uses ${api.name} (${api.since})`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('still guards the API that broke Monterey', () => {
    // Sanity: AbortSignal.timeout IS used by app code, so the exemption above
    // is load-bearing — if the polyfill is deleted the exemption must go too.
    const users = walk(SRC).filter(
      (f) =>
        !f.endsWith(path.join('utils', 'webCompat.js')) &&
        /\bAbortSignal\s*\.\s*timeout\b/.test(fs.readFileSync(f, 'utf8')),
    );
    expect(users.length).toBeGreaterThan(0);

    const compat = fs.readFileSync(path.join(SRC, 'utils', 'webCompat.js'), 'utf8');
    expect(compat).toMatch(/AbortSignal\.timeout\s*=/);
  });
});
