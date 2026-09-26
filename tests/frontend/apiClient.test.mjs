// Unit tests for electron/src/shared/api/client.ts URL composition + error handling.
// Runs under node:test with a synthetic fetch mock so no backend is needed.

import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

// bun/node strip .ts extension when type='module' is set in package.json;
// without that we load via bun's loader by requesting the .ts path.
const clientPath = new URL('../../electron/src/shared/api/client.ts', import.meta.url).pathname;
const { API, apiUrl, apiFetch, apiJson, apiPost, ApiError } = await import(clientPath);


test('apiUrl falls back to API root on empty input', () => {
  assert.equal(apiUrl(), API);
  assert.equal(apiUrl(''), API);
});

test('apiUrl prepends slash when missing', () => {
  assert.equal(apiUrl('engines'), `${API}/engines`);
  assert.equal(apiUrl('/engines'), `${API}/engines`);
});

test('apiUrl passes absolute URLs through untouched', () => {
  assert.equal(apiUrl('https://example.com/foo'), 'https://example.com/foo');
  assert.equal(apiUrl('http://localhost:9000/bar'), 'http://localhost:9000/bar');
});

test('ApiError carries status + detail', () => {
  const err = new ApiError('boom', { status: 503, detail: { code: 'x' } });
  assert.equal(err.name, 'ApiError');
  assert.equal(err.message, 'boom');
  assert.equal(err.status, 503);
  assert.deepEqual(err.detail, { code: 'x' });
});

test('apiFetch resolves on 2xx', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = mock.fn(async () => new Response('ok', { status: 200 }));
  try {
    const res = await apiFetch('/ping');
    assert.equal(res.status, 200);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('apiFetch throws ApiError with JSON detail on non-2xx', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = mock.fn(async () =>
    new Response(JSON.stringify({ detail: 'Job not found' }), {
      status: 404, statusText: 'Not Found',
      headers: { 'Content-Type': 'application/json' },
    }),
  );
  try {
    await assert.rejects(
      () => apiFetch('/dub/x'),
      (err) => {
        assert.ok(err instanceof ApiError);
        assert.equal(err.status, 404);
        assert.equal(err.detail, 'Job not found');
        assert.match(err.message, /404/);
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('apiJson parses 2xx body', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = mock.fn(async () =>
    new Response(JSON.stringify({ ok: true, n: 42 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }),
  );
  try {
    const body = await apiJson('/ping');
    assert.deepEqual(body, { ok: true, n: 42 });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('apiPost json body sets Content-Type + stringified body', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = mock.fn(async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ received: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  });
  try {
    await apiPost('/models/install', { repo_id: 'k2-fsa/OmniVoice' });
    assert.equal(calls.length, 1);
    const { init } = calls[0];
    assert.equal(init.method, 'POST');
    assert.equal(new Headers(init.headers).get('content-type'), 'application/json');
    assert.equal(init.body, JSON.stringify({ repo_id: 'k2-fsa/OmniVoice' }));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('apiPost passes FormData without stringify + no Content-Type override', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = mock.fn(async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({}), { status: 200 });
  });
  try {
    const fd = new FormData();
    fd.append('text', 'hello');
    await apiPost('/generate', fd);
    assert.equal(calls[0].init.body, fd);
    // Browser sets multipart boundary; we must NOT force a JSON header.
    assert.equal(new Headers(calls[0].init.headers).has('content-type'), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
