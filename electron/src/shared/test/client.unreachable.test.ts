import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { apiFetch, ApiError } from '../api/client';
import {
  recordBackendContact,
  lastBackendContact,
  describeLastContact,
  unreachableBackendMessage,
  contactAge,
  _resetBackendContactForTests,
} from '../utils/backendContact';
import { detectDeploymentMode } from '../utils/deploymentMode';

// #1164: "Can't reach the local VoiceStudio backend" reported from a
// `bun run dev` browser session with ZERO diagnostics. The give-up error must
// now (a) know which deployment it's in — there is no app to restart and no
// Settings → Logs in a browser tab — and (b) say whether the backend ever
// answered this session: "it was answering Xs ago and stopped" (crashed
// mid-session) is a different bug report from "it never answered" (never
// started). vitest runs outside Tauri with import.meta.env.DEV=true → 'dev'.

const CASCADE_MS = 400 + 900 + 1600;

describe('detectDeploymentMode', () => {
  it('classifies the three runtime contexts', () => {
    expect(detectDeploymentMode({}, { __TAURI__: {} })).toBe('desktop');
    expect(detectDeploymentMode({ DEV: true }, { __TAURI_INTERNALS__: {} })).toBe('desktop');
    expect(detectDeploymentMode({ DEV: true }, {})).toBe('dev');
    expect(detectDeploymentMode({}, {})).toBe('server');
    expect(detectDeploymentMode(undefined, undefined)).toBe('server');
  });
});

describe('backendContact', () => {
  beforeEach(() => _resetBackendContactForTests());
  afterEach(() => _resetBackendContactForTests());

  it('records and reads back the last contact (surviving module state loss via sessionStorage)', () => {
    expect(lastBackendContact()).toBeNull();
    recordBackendContact(1_000_000);
    expect(lastBackendContact()).toBe(1_000_000);
    expect(sessionStorage.getItem('ov_last_backend_contact')).toBe('1000000');
  });

  it('formats contact age on the crashAge scale', () => {
    expect(contactAge(0, 4_000)).toBe('4 s');
    expect(contactAge(0, 180_000)).toBe('3 min');
    expect(contactAge(0, 2 * 3600_000)).toBe('2 h');
  });

  it('tells the "was answering, then stopped" story when contact exists', () => {
    recordBackendContact(10_000);
    const phrase = describeLastContact(14_000);
    expect(phrase).toContain('4 s');
    expect(phrase).toMatch(/stopped responding/);
  });

  it('tells the "never answered" story when there was no contact', () => {
    expect(describeLastContact()).toMatch(/has not answered at all this session/);
  });

  it('gives dev-mode advice pointing at the bun run dev terminal and omnivoice.log', () => {
    const msg = unreachableBackendMessage('dev');
    expect(msg).toContain("Can't reach the local VoiceStudio backend");
    expect(msg).toContain('bun run dev');
    expect(msg).toContain('omnivoice.log');
    expect(msg).not.toContain('restart the app');
  });

  it('gives server-mode advice pointing at container/server logs', () => {
    const msg = unreachableBackendMessage('server');
    expect(msg).toContain('docker logs');
    expect(msg).toContain('journalctl');
    expect(msg).toMatch(/page itself can go down/);
  });
});

describe('apiFetch — mode-aware honest give-up (#1164)', () => {
  beforeEach(() => _resetBackendContactForTests());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    _resetBackendContactForTests();
  });

  it('says the backend never answered when there was no contact this session', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const p = apiFetch('/model/status');
    const assertion = expect(p).rejects.toMatchObject({
      status: 0,
      message: expect.stringMatching(/has not answered at all this session[\s\S]*bun run dev/),
    });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 100);
    await assertion;
  });

  it('says the backend WAS answering and stopped — including via an HTTP error response', async () => {
    vi.useFakeTimers();
    // First request gets an HTTP 500: the backend answered (alive!), which
    // must count as contact. Then the process dies and transport fails.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('boom', { status: 500, statusText: 'Server Error' }))
      .mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/x')).rejects.toMatchObject({ status: 500 });
    expect(lastBackendContact()).not.toBeNull();

    const p = apiFetch('/generate');
    const assertion = expect(p).rejects.toMatchObject({
      status: 0,
      message: expect.stringMatching(/was answering \d+ (s|min) ago and then stopped responding/),
    });
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 100);
    await assertion;
  });

  it('attaches {mode, lastContactMs, firstFailureTs, attempts} to the ApiError detail', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    recordBackendContact(123_456);

    const p = apiFetch('/model/status');
    const settled = p.catch((e: ApiError) => e);
    await vi.advanceTimersByTimeAsync(CASCADE_MS + 100);
    const err = (await settled) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toMatchObject({
      mode: 'dev',
      lastContactMs: 123_456,
      attempts: 4, // initial try + 3 bounded retries
      transport: expect.stringContaining('Failed to fetch'),
    });
    expect((err.detail as { firstFailureTs: number }).firstFailureTs).toBeGreaterThan(0);
  });

  it('records contact on every successful response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('ok', { status: 200 })));
    expect(lastBackendContact()).toBeNull();
    await apiFetch('/health');
    expect(lastBackendContact()).not.toBeNull();
  });
});
