import { test } from 'node:test';
import assert from 'node:assert/strict';
import { request as httpRequest } from 'node:http';
import { startHealthBackend } from './fake-health-backend.mjs';

const get = (backend, path, init) => fetch(`http://127.0.0.1:${backend.port}${path}`, init);

test('defaults answer /health', async () => {
  const backend = await startHealthBackend();
  try {
    const response = await get(backend, '/health');
    assert.deepEqual(await response.json(), { status: 'ok', version: 'native-smoke' });
  } finally {
    await backend.close();
  }
});

test('port option binds that port', async () => {
  const probe = await startHealthBackend();
  const port = probe.port;
  await probe.close();
  const backend = await startHealthBackend({ port });
  try {
    assert.equal(backend.port, port);
    assert.equal((await get(backend, '/health')).status, 200);
  } finally {
    await backend.close();
  }
});

test('override replaces one fixture', async () => {
  const analytics = { available: true, prompted: false, opted_in: false };
  const backend = await startHealthBackend({
    fixtures: { '/api/settings/analytics': analytics },
  });
  try {
    const response = await get(backend, '/api/settings/analytics');
    assert.deepEqual(await response.json(), analytics);
  } finally {
    await backend.close();
  }
});

test('unknown path is 404 and recorded', async () => {
  const backend = await startHealthBackend();
  try {
    const response = await get(backend, '/nope');
    assert.equal(response.status, 404);
    assert.deepEqual(backend.requests.at(-1), {
      method: 'GET',
      path: '/nope',
      status: 404,
      body: '',
    });
  } finally {
    await backend.close();
  }
});

test('/ws/events completes a WebSocket handshake and stays open', async () => {
  const backend = await startHealthBackend();
  try {
    const status = await new Promise((resolve, reject) => {
      const request = httpRequest({
        host: '127.0.0.1',
        port: backend.port,
        path: '/ws/events',
        headers: {
          Connection: 'Upgrade',
          Upgrade: 'websocket',
          'Sec-WebSocket-Version': '13',
          'Sec-WebSocket-Key': Buffer.from('the sample nonce').toString('base64'),
        },
      });
      request.once('upgrade', (response, socket) => {
        socket.destroy();
        resolve({
          code: response.statusCode,
          accept: response.headers['sec-websocket-accept'],
        });
      });
      request.once('response', (response) =>
        reject(new Error(`No upgrade, got HTTP ${response.statusCode}`)),
      );
      request.once('error', reject);
      request.end();
    });
    assert.deepEqual(status, { code: 101, accept: 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=' });
  } finally {
    await backend.close();
  }
});

test('install status and generate budget have defaults', async () => {
  const backend = await startHealthBackend();
  try {
    assert.deepEqual(await (await get(backend, '/models/install/status')).json(), { jobs: [] });
    assert.deepEqual(await (await get(backend, '/generate/budget')).json(), {});
  } finally {
    await backend.close();
  }
});

test('POST body is recorded', async () => {
  const backend = await startHealthBackend();
  try {
    await get(backend, '/api/settings/analytics', {
      method: 'POST',
      body: '{"opted_in":false}',
    });
    assert.equal(backend.requests.at(-1).body, '{"opted_in":false}');
  } finally {
    await backend.close();
  }
});

test('diagnostic consent is returning-off, persists per server, and is independent of analytics', async () => {
  const backend = await startHealthBackend();
  try {
    const response = await get(backend, '/api/settings/diagnostics');
    assert.equal(response.status, 200);
    const first = await response.json();
    assert.equal(first.available, true);
    assert.equal(first.prompted, true);
    assert.equal(first.opted_in, false);
    assert.equal(first.enabled, false);
    const analytics = await (await get(backend, '/api/settings/analytics')).json();
    for (const enabled of [true, false]) {
      const saved = await (
        await get(backend, '/api/settings/diagnostics', {
          method: 'PUT',
          body: JSON.stringify({ enabled }),
        })
      ).json();
      assert.equal(saved.enabled, enabled);
      assert.equal(saved.opted_in, enabled);
      assert.equal(saved.prompted, true);
      assert.deepEqual(await (await get(backend, '/api/settings/diagnostics')).json(), saved);
      assert.deepEqual(await (await get(backend, '/api/settings/analytics')).json(), analytics);
      if (enabled) {
        const other = await startHealthBackend();
        try {
          assert.equal(
            (await (await get(other, '/api/settings/diagnostics')).json()).enabled,
            false,
          );
        } finally {
          await other.close();
        }
      }
    }
    for (const body of [
      '{',
      'null',
      '[]',
      '{}',
      '{"enabled":"true"}',
      '{"enabled":1}',
      '{"enabled":true,"analytics":true}',
    ]) {
      const rejected = await get(backend, '/api/settings/diagnostics', { method: 'PUT', body });
      assert.equal(rejected.status, 422);
      const unchanged = await (await get(backend, '/api/settings/diagnostics')).json();
      assert.equal(unchanged.enabled, false);
      assert.equal(unchanged.opted_in, false);
      assert.equal(unchanged.prompted, true);
    }
  } finally {
    await backend.close();
  }
});

test('diagnostic events require the exact owner grant and keep controls out of metadata', async () => {
  const backend = await startHealthBackend();
  const input = {
    category: 'operation_failed',
    source: 'renderer',
    stage: 'other',
    error_type: 'Error',
  };
  const post = (body) =>
    get(backend, '/api/settings/diagnostics/event', { method: 'POST', body: JSON.stringify(body) });
  const consent = async (enabled) =>
    (
      await get(backend, '/api/settings/diagnostics', {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      })
    ).json();
  try {
    assert.equal(
      (await (await get(backend, '/api/settings/diagnostics')).json()).grant_epoch,
      null,
    );
    assert.deepEqual(await (await post(input)).json(), { sent: false });
    const first = await consent(true);
    assert.match(first.grant_epoch, /^[a-f0-9]{32}$/);
    assert.equal((await consent(true)).grant_epoch, first.grant_epoch);
    assert.deepEqual(await (await post({ ...input, expected_epoch: first.grant_epoch })).json(), {
      sent: true,
    });
    assert.deepEqual(backend.diagnosticEvents, [input]);
    await consent(false);
    const fresh = await consent(true);
    assert.notEqual(fresh.grant_epoch, first.grant_epoch);
    assert.deepEqual(await (await post({ ...input, expected_epoch: first.grant_epoch })).json(), {
      sent: false,
    });
    for (const rejected of [
      { ...input, message: 'private' },
      { ...input, expected_epoch: 'invalid' },
      { ...input, category: 'private' },
      { ...input, frames: '/home/private:1' },
    ])
      assert.equal((await post(rejected)).status, 422);
    assert.deepEqual(backend.diagnosticEvents, [input]);
  } finally {
    await backend.close();
  }
});

test('a fresh diagnostic choice can override the returning-user fixture', async () => {
  const fresh = {
    available: true,
    prompted: false,
    opted_in: false,
    enabled: false,
    grant_epoch: null,
  };
  const backend = await startHealthBackend({ fixtures: { '/api/settings/diagnostics': fresh } });
  try {
    assert.deepEqual(await (await get(backend, '/api/settings/diagnostics')).json(), fresh);
  } finally {
    await backend.close();
  }
});
