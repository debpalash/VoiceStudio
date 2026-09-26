import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  _adaptLastRunCrash,
  acknowledgeBackendCrash,
  describeCrashExit,
  getLastBackendCrash,
  getUnacknowledgedBackendCrash,
  type LastRunCrashRecord,
} from '../utils/backendCrash';
import { ADMIN_SESSION_STORAGE_KEY } from '../api/authSession';

// #1164: outside the Tauri shell the crash getters must fall back to the
// backend's own run-sentinel forensics (GET /system/last-run-crash) and adapt
// the record to the CrashMarker shape — so BackendCrashNotice, the apiFetch
// crash branch, and the bug-report prefill light up in browser/dev/Docker
// with zero changes of their own. vitest runs outside Tauri, so the fallback
// path is the one under test.

function record(overrides: Partial<LastRunCrashRecord> = {}): LastRunCrashRecord {
  const now = Math.floor(Date.now() / 1000);
  return {
    detected_at: now - 60,
    started_at: now - 600,
    ended_between: [now - 90, now - 60],
    uptime_hint_s: 510,
    version: '0.3.23',
    last_activity: { ts: now - 90, kind: 'transcribe', detail: 'dub' },
    log_tail: ['INFO starting transcription', 'INFO loading ASR model'],
    ...overrides,
  };
}

function okResponse(rec: LastRunCrashRecord | null, acknowledged = false) {
  return new Response(JSON.stringify({ record: rec, acknowledged }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('_adaptLastRunCrash — run-sentinel record → CrashMarker shape', () => {
  it('maps the record so the existing crash UI can render it', () => {
    const sourceRecord = record();
    const marker = _adaptLastRunCrash(sourceRecord, false);
    expect(marker.exit_code).toBeNull();
    expect(marker.signal).toBeNull();
    // describeCrashExit falls through to exit_desc on a null code+signal.
    expect(describeCrashExit(marker)).toBe('process ended uncleanly (previous run)');
    expect(marker.backend_version).toBe('0.3.23');
    expect(marker.uptime_s).toBe(510);
    expect(marker.ts).toBe(sourceRecord.detected_at);
    expect(marker.acknowledged).toBe(false);
    // The "stderr" evidence carries the last activity + the scrubbed log tail.
    expect(marker.last_stderr).toContain('last activity before the death: transcribe (dub)');
    expect(marker.last_stderr).toContain('INFO loading ASR model');
  });

  it('tolerates a sparse record (no activity, no uptime, no tail)', () => {
    const marker = _adaptLastRunCrash(
      record({ last_activity: null, uptime_hint_s: null, log_tail: [] }),
      true,
    );
    expect(marker.uptime_s).toBe(0);
    expect(marker.last_stderr).toBe('');
    expect(marker.acknowledged).toBe(true);
  });
});

describe('browser fallback — getLastBackendCrash / ack over HTTP', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('fetches GET /system/last-run-crash and adapts the record', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse(record()));
    vi.stubGlobal('fetch', fetchMock);

    const marker = await getLastBackendCrash();
    expect(marker).not.toBeNull();
    expect(marker?.exit_desc).toBe('process ended uncleanly (previous run)');
    expect(String(fetchMock.mock.calls[0][0])).toContain('/system/last-run-crash');
  });

  it('uses only the short-lived session and deletes a legacy master', async () => {
    const { API } = await import('../api/client');
    const session = `ovs_admin_session_${'S'.repeat(43)}`;
    sessionStorage.setItem(
      ADMIN_SESSION_STORAGE_KEY,
      JSON.stringify({ token: session, expiresAt: Date.now() / 1000 + 3600, apiBase: API }),
    );
    localStorage.setItem('ov_api_key', 'legacy-master');
    const fetchMock = vi.fn().mockResolvedValue(okResponse(record()));
    vi.stubGlobal('fetch', fetchMock);

    await getLastBackendCrash();

    const options = fetchMock.mock.calls[0][1];
    expect(new Headers(options.headers).get('Authorization')).toBe(`Bearer ${session}`);
    expect(new Headers(options.headers).get('Authorization')).not.toContain('legacy-master');
    expect(options.credentials).toBe('include');
    expect(localStorage.getItem('ov_api_key')).toBeNull();
  });

  it('getUnacknowledgedBackendCrash filters an already-acknowledged record', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okResponse(record(), true)));
    expect(await getUnacknowledgedBackendCrash()).toBeNull();
  });

  it('resolves null when the backend is down (fetch rejects) — never throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    expect(await getLastBackendCrash()).toBeNull();
  });

  it('resolves null on a non-OK response and on an empty record', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 403 })));
    expect(await getLastBackendCrash()).toBeNull();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okResponse(null)));
    expect(await getLastBackendCrash()).toBeNull();
  });

  it('acknowledgeBackendCrash POSTs the ack endpoint (and swallows failures)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await acknowledgeBackendCrash();
    expect(String(fetchMock.mock.calls[0][0])).toContain('/system/last-run-crash/ack');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('down')));
    await expect(acknowledgeBackendCrash()).resolves.toBeUndefined();
  });
});
