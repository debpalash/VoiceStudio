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
