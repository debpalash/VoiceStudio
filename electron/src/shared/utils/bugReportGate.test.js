/**
 * openBugReport / checkBuildFreshness — the stale-build deflection gate.
 *
 * 6 in 10 sampled "can't reach the backend" reports came from builds that
 * were already obsolete when filed. The gate offers the latest release
 * before the prefill opens, with a file-anyway escape hatch, and stamps a
 * triage-greppable `**Build status:**` line into the report body.
 *
 * Module-level state (the session freshness cache, the build-time
 * APP_VERSION constant) means every test loads a fresh module copy via
 * vi.resetModules() + dynamic import.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const askConfirm = vi.fn();
const openExternal = vi.fn().mockResolvedValue(undefined);
const getState = vi.fn();

vi.mock('./dialog', () => ({ askConfirm: (...a) => askConfirm(...a) }));
vi.mock('../api/external', () => ({ openExternal: (...a) => openExternal(...a) }));
vi.mock('../store', () => ({ useAppStore: { getState: (...a) => getState(...a) } }));
// As in bugReport.test.js: only the shell bridge is mocked (resolves null,
// like a non-Tauri context).
vi.mock('./backendCrash', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getLastBackendCrash: vi.fn().mockResolvedValue(null) };
});

/** Fetch stub: GitHub latest-release answers with `tag`; the backend
 * context endpoints (system/info, engines) stay unreachable — the exact
 * shape of the "can't reach the backend" reports this gate exists for. */
function stubFetch({ tag } = {}) {
  const impl = vi.fn(async (url) => {
    if (String(url).includes('api.github.com')) {
      if (!tag) throw new Error('offline');
      return { ok: true, json: async () => ({ tag_name: tag }) };
    }
    throw new Error('ECONNREFUSED');
  });
  vi.stubGlobal('fetch', impl);
  return impl;
}

async function loadGate(appVersion) {
  vi.stubGlobal('__APP_VERSION__', appVersion);
  vi.resetModules();
  return import('./bugReport');
}

function openedUrls() {
  return openExternal.mock.calls.map(([u]) => String(u));
}

function decodedIssueBody() {
  const url = openedUrls().find((u) => u.includes('/issues/new'));
  expect(url).toBeDefined();
  return decodeURIComponent(url.split('&body=')[1]);
}

beforeEach(() => {
  askConfirm.mockReset();
  openExternal.mockReset().mockResolvedValue(undefined);
  getState.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.__TAURI_INTERNALS__;
});

describe('openBugReport — browser/dev/Docker (GitHub latest-release source)', () => {
  it('outdated build, user takes the update: opens the release page, files nothing', async () => {
    stubFetch({ tag: 'v9.9.9' });
    askConfirm.mockResolvedValue(true);
    const { openBugReport } = await loadGate('0.3.7');

    await openBugReport({ error: new Error('boom') });

    expect(askConfirm).toHaveBeenCalledTimes(1);
    // Interpolated versions reach the prompt (i18next falls back to the raw
    // key here, so assert on the arguments instead of rendered text).
    expect(askConfirm.mock.calls[0][2]).toEqual({
      okLabel: expect.any(String),
      cancelLabel: expect.any(String),
    });
    expect(openedUrls()).toEqual(['https://github.com/debpalash/VoiceStudio/releases/latest']);
  });

  it('outdated build, file anyway: prefill opens and carries the OUTDATED marker', async () => {
    stubFetch({ tag: 'v9.9.9' });
    askConfirm.mockResolvedValue(false);
    const { openBugReport } = await loadGate('0.3.7');

    await openBugReport({ error: new Error('boom') });

    const body = decodedIssueBody();
    expect(body).toContain('**Build status:** OUTDATED');
    expect(body).toContain('`v9.9.9`');
  });

  it('current build: no prompt, body records "current at filing time"', async () => {
    stubFetch({ tag: 'v9.9.9' });
    const { openBugReport } = await loadGate('9.9.9');

    await openBugReport({});

    expect(askConfirm).not.toHaveBeenCalled();
    expect(decodedIssueBody()).toContain('current at filing time (latest `v9.9.9`)');
  });

  it('freshness unknown (GitHub unreachable): no prompt, no marker, report still opens', async () => {
    stubFetch({});
    const { openBugReport } = await loadGate('0.3.7');

    await openBugReport({});

    expect(askConfirm).not.toHaveBeenCalled();
    expect(decodedIssueBody()).not.toContain('Build status');
  });

  it("an 'unknown' dev build is never nudged and never claims to be current", async () => {
    const fetchImpl = stubFetch({ tag: 'v9.9.9' });
    const { openBugReport } = await loadGate(undefined);

    await openBugReport({});

    expect(askConfirm).not.toHaveBeenCalled();
    expect(decodedIssueBody()).not.toContain('Build status');
    // Unparseable version short-circuits before any network round-trip.
    expect(fetchImpl.mock.calls.every(([u]) => !String(u).includes('api.github.com'))).toBe(true);
  });

  it('caches the freshness verdict for the session (one GitHub call across reports)', async () => {
    const fetchImpl = stubFetch({ tag: 'v9.9.9' });
    askConfirm.mockResolvedValue(false);
    const { openBugReport } = await loadGate('0.3.7');

    await openBugReport({});
    await openBugReport({});

    const ghCalls = fetchImpl.mock.calls.filter(([u]) => String(u).includes('api.github.com'));
    expect(ghCalls).toHaveLength(1);
  });
});

describe('openBugReport — desktop (Rust updater verdict from the store)', () => {
  it("updater says 'available': prompts without touching the network", async () => {
    window.__TAURI_INTERNALS__ = {};
    const fetchImpl = stubFetch({ tag: 'v9.9.9' });
    getState.mockReturnValue({ updateStatus: 'available', updateVersion: '9.9.9' });
    askConfirm.mockResolvedValue(true);
    const { openBugReport } = await loadGate('0.3.7');

    await openBugReport({});

    expect(askConfirm).toHaveBeenCalledTimes(1);
    expect(openedUrls()).toEqual(['https://github.com/debpalash/VoiceStudio/releases/latest']);
    expect(fetchImpl.mock.calls.every(([u]) => !String(u).includes('api.github.com'))).toBe(true);
  });

  it("updater 'idle' (up-to-date or unchecked): stays silent rather than guessing", async () => {
    window.__TAURI_INTERNALS__ = {};
    stubFetch({ tag: 'v9.9.9' });
    getState.mockReturnValue({ updateStatus: 'idle', updateVersion: null });
    const { openBugReport } = await loadGate('0.3.7');

    await openBugReport({});

    expect(askConfirm).not.toHaveBeenCalled();
    expect(decodedIssueBody()).not.toContain('Build status');
  });
});
