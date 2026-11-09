import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import test from 'node:test';
import { freePort, isPortConflict, launchWithFreshPorts } from './desktop-harness.mjs';

const conflict = () =>
  new Error(
    'electron.launch: closed\n[startup] Error: listen EADDRINUSE: address already in use 127.0.0.1:39081',
  );

test('only a port collision is retried, with fresh ports each time', async () => {
  let calls = 0;
  let refreshed = 0;
  const result = await launchWithFreshPorts(
    async () => {
      calls += 1;
      if (calls < 3) throw conflict();
      return 'launched';
    },
    async () => {
      refreshed += 1;
    },
  );
  assert.equal(result, 'launched');
  assert.equal(calls, 3);
  assert.equal(refreshed, 2);
});

test('gives up after the attempt limit and surfaces the collision', async () => {
  let calls = 0;
  await assert.rejects(
    launchWithFreshPorts(
      async () => {
        calls += 1;
        throw conflict();
      },
      async () => {},
    ),
    /EADDRINUSE/,
  );
  assert.equal(calls, 3);
});

test('any other launch failure is thrown immediately', async () => {
  let calls = 0;
  await assert.rejects(
    launchWithFreshPorts(
      async () => {
        calls += 1;
        throw new Error('renderer crashed');
      },
      async () => assert.fail('must not pick new ports'),
    ),
    /renderer crashed/,
  );
  assert.equal(calls, 1);
  assert.equal(isPortConflict(new Error('boom')), false);
});

test('freePort returns a port that can be bound right away', async () => {
  const port = await freePort();
  await new Promise((resolve, reject) => {
    const server = createServer().once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
});
