// Unit tests for scripts/dev-backend.mjs — the dev:api wrapper that turns a
// silent backend death into a loud, recoverable exit banner (#1164, #1690).
//
// Load-bearing guarantees:
//   * the wrapper launches uvicorn directly and owns Python source reloads, so
//     a dead server cannot hide behind a still-live uvicorn reload parent;
//   * the data-dir resolution mirrors backend/core/config.py, so the banner
//     tails the same omnivoice.log the backend writes;
//   * the banner names the exit code/signal, carries the log tail, flags the
//     OOM-kill shapes, and points at the next-start crash notice.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  buildExitBanner,
  createBackendSupervisor,
  isBackendSourceChange,
  killProcessTree,
  resolveDataDir,
  tailFile,
  uvRunArgs,
  uvicornArgs,
} from '../../scripts/dev-backend.mjs';

test('CPU/CUDA/ROCm restarts skip sync while preserving bind-host and port overrides', () => {
  for (const variant of ['auto', 'cpu', 'cuda', 'rocm']) {
    const args = uvRunArgs({
      OMNIVOICE_TORCH_VARIANT: variant,
      OMNIVOICE_BIND_HOST: '192.0.2.10',
      OMNIVOICE_PORT: '4321',
    });
    assert.deepEqual(args.slice(0, 2), ['run', '--no-sync']);
    assert.equal(args[args.indexOf('--host') + 1], '192.0.2.10');
    assert.equal(args[args.indexOf('--port') + 1], '4321');
  }
});

function supervisorHarness(overrides = {}) {
  const children = [];
  const timers = [];
  const exits = [];
  const reports = [];
  const watchers = [];
  let timestamp = 1_000;

  const supervisor = createBackendSupervisor({
    spawnBackend: () => {
      const child = new EventEmitter();
      child.killedWith = null;
      child.kill = (signal) => {
        child.killedWith = signal;
      };
      children.push(child);
      return child;
    },
    watchBackend: (onChange) => {
      const watcher = new EventEmitter();
      watcher.closed = false;
      watcher.close = () => {
        watcher.closed = true;
      };
      watcher.onChange = onChange;
      watchers.push(watcher);
      return watcher;
    },
    schedule: (callback, delay) => {
      const timer = { callback, delay, cancelled: false };
      timers.push(timer);
      return timer;
    },
    cancelSchedule: (timer) => {
      timer.cancelled = true;
    },
    now: () => timestamp,
    exit: (code) => exits.push(code),
    report: (message) => reports.push(message),
    dataDir: () => '/missing-test-data',
    ...overrides,
  });

  return {
    children,
    exits,
    reports,
    supervisor,
    timers,
    watchers,
    advance(ms) {
      timestamp += ms;
    },
    runNextTimer() {
      const timer = timers.find((candidate) => !candidate.cancelled && !candidate.ran);
      assert.ok(timer, 'expected a pending restart timer');
      timer.ran = true;
      timer.callback();
    },
  };
}

test('uvicorn runs directly on loopback because the wrapper owns source reloads', () => {
  assert.equal(
    ['uv', ...uvicornArgs({})].join(' '),
    'uv run uvicorn main:app --app-dir backend --host 127.0.0.1 --port 3900',
  );
  assert.equal(uvicornArgs({}).includes('--reload'), false);
});

test('dev backend honours the backend bind host and port knobs', () => {
  const args = uvicornArgs({ OMNIVOICE_BIND_HOST: '0.0.0.0', OMNIVOICE_PORT: '3912' });
  assert.deepEqual(args.slice(-4), ['--host', '0.0.0.0', '--port', '3912']);
  for (const bad of ['', 'nope', '0', '70000']) {
    assert.deepEqual(uvicornArgs({ OMNIVOICE_PORT: bad }).slice(-2), ['--port', '3900']);
  }
  assert.deepEqual(uvicornArgs({ OMNIVOICE_BIND_HOST: '  ' }).slice(-4, -2), ['--host', '127.0.0.1']);
  assert.deepEqual(uvRunArgs({ OMNIVOICE_TORCH_VARIANT: 'rocm', OMNIVOICE_PORT: '3912' }).slice(0, 3), [
    'run',
    '--no-sync',
    'uvicorn',
  ]);
});

test('only Python source changes trigger backend reloads', () => {
  assert.equal(isBackendSourceChange('api/router.py'), true);
  assert.equal(isBackendSourceChange('API/ROUTER.PY'), true);
  assert.equal(isBackendSourceChange('api/__pycache__/router.pyc'), false);
  assert.equal(isBackendSourceChange(null), false);
});

test('resolveDataDir mirrors backend/core/config.py::get_app_data_dir', () => {
  assert.equal(resolveDataDir({ OMNIVOICE_DATA_DIR: '/x' }, 'linux', '/home/u'), '/x');
  assert.equal(
    resolveDataDir({}, 'darwin', '/Users/u'),
    path.join('/Users/u', 'Library/Application Support/OmniVoice'),
  );
  assert.equal(
    resolveDataDir({ APPDATA: 'C:\\Users\\u\\AppData\\Roaming' }, 'win32', 'C:\\Users\\u'),
    path.join('C:\\Users\\u\\AppData\\Roaming', 'OmniVoice'),
  );
  assert.equal(resolveDataDir({}, 'linux', '/home/u'), path.join('/home/u', '.omnivoice'));
});

test('tailFile returns the last N lines, and null for a missing file', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'ov-devbackend-'));
  const log = path.join(dir, 'omnivoice.log');
  writeFileSync(log, ['a', 'b', 'c', 'd', ''].join('\n'));
  assert.equal(tailFile(log, 2), 'c\nd');
  assert.equal(tailFile(path.join(dir, 'missing.log'), 2), null);
});

test('banner names the exit, embeds the log tail, and points at the crash notice', () => {
  const banner = buildExitBanner({
    code: 1,
    signal: null,
    logTail: 'ERROR the last thing logged',
    logPath: '/data/omnivoice.log',
    platform: 'darwin',
  });
  assert.match(banner, /OMNIVOICE BACKEND DIED/);
  assert.match(banner, /exit code 1/);
  assert.match(banner, /ERROR the last thing logged/);
  assert.match(banner, /crash notice in the UI the next time/);
  assert.doesNotMatch(banner, /journalctl/, 'the journalctl hint is Linux-only');
});

test('banner flags OOM-kill shapes and adds the Linux journalctl hint', () => {
  const killed = buildExitBanner({
    code: null,
    signal: 'SIGKILL',
    logTail: null,
    logPath: '/data/omnivoice.log',
    platform: 'linux',
  });
  assert.match(killed, /killed by signal SIGKILL/);
  assert.match(killed, /out-of-memory killer/);
  assert.match(killed, /journalctl -k \| grep -i oom/);
  assert.match(killed, /no omnivoice\.log found/);

  const oom137 = buildExitBanner({
    code: 137,
    signal: null,
    logTail: '',
    logPath: '/p',
    platform: 'linux',
  });
  assert.match(oom137, /out-of-memory killer/);
});

test('an unexpected backend death is restarted instead of tearing down dev', () => {
  const harness = supervisorHarness();
  harness.supervisor.start();

  harness.children[0].emit('exit', 1, null);

  assert.deepEqual(harness.exits, []);
  assert.equal(harness.timers[0].delay, 1_000);
  assert.match(harness.reports.at(-1), /restarting in 1s \(1\/3\)/);

  harness.runNextTimer();
  assert.equal(harness.children.length, 2);
});

test('Python edits reload the direct worker without counting as crashes', () => {
  const harness = supervisorHarness();
  harness.supervisor.start();

  harness.watchers[0].onChange('api/router.py');
  assert.equal(harness.timers[0].delay, 250);
  harness.runNextTimer();
  assert.equal(harness.children[0].killedWith, 'SIGTERM');

  harness.children[0].emit('exit', null, 'SIGTERM');

  assert.equal(harness.children.length, 2);
  assert.deepEqual(harness.exits, []);
  assert.doesNotMatch(harness.reports.join('\n'), /BACKEND DIED|restarting in/);
});

test('a crash racing a requested reload still uses crash recovery', () => {
  for (const [code, signal] of [
    [1, null],
    [null, 'SIGKILL'],
  ]) {
    const harness = supervisorHarness();
    harness.supervisor.start();
    harness.watchers[0].onChange('api/router.py');
    harness.runNextTimer();

    harness.children[0].emit('exit', code, signal);

    assert.deepEqual(harness.exits, []);
    assert.match(harness.reports.join('\n'), /BACKEND DIED/);
    assert.match(harness.reports.at(-1), /restarting in 1s \(1\/3\)/);
    harness.runNextTimer();
    assert.equal(harness.children.length, 2);
  }
});

test('the supervisor stops after three restarts inside the crash window', () => {
  const harness = supervisorHarness();
  harness.supervisor.start();

  for (let crash = 0; crash < 3; crash += 1) {
    harness.children.at(-1).emit('exit', 1, null);
    harness.runNextTimer();
  }
  harness.children.at(-1).emit('exit', 7, null);

  assert.deepEqual(harness.exits, [7]);
  assert.match(harness.reports.at(-1), /stopped after 3 restarts in 60s/);
  assert.equal(harness.children.length, 4);
});

test('old crashes expire from the bounded restart window', () => {
  const harness = supervisorHarness();
  harness.supervisor.start();

  for (let crash = 0; crash < 3; crash += 1) {
    harness.children.at(-1).emit('exit', 1, null);
    harness.runNextTimer();
  }
  harness.advance(60_000);
  harness.children.at(-1).emit('exit', 1, null);

  assert.deepEqual(harness.exits, []);
  assert.match(harness.reports.at(-1), /restarting in 1s \(1\/3\)/);
});

test('a deliberate stop never restarts the backend', () => {
  const harness = supervisorHarness();
  harness.supervisor.start();

  harness.supervisor.stop('SIGTERM');
  assert.equal(harness.children[0].killedWith, 'SIGTERM');
  assert.equal(harness.watchers[0].closed, true);
  harness.children[0].emit('exit', null, 'SIGTERM');

  assert.deepEqual(harness.exits, [1]);
  assert.equal(harness.timers.length, 0);
  assert.doesNotMatch(harness.reports.join('\n'), /restarting/);
});

test('a source watcher failure tears down dev with a failing exit', () => {
  const harness = supervisorHarness();
  harness.supervisor.start();

  harness.watchers[0].emit('error', new Error('watch handle closed'));
  assert.equal(harness.children[0].killedWith, 'SIGTERM');
  harness.children[0].emit('exit', 0, null);

  assert.deepEqual(harness.exits, [1]);
  assert.match(harness.reports.at(-1), /source watcher failed: watch handle closed/);
});

test('failure to spawn uv exits immediately instead of looping', () => {
  const exits = [];
  const reports = [];
  const supervisor = createBackendSupervisor({
    spawnBackend: () => {
      throw new Error('uv is missing');
    },
    exit: (code) => exits.push(code),
    report: (message) => reports.push(message),
    watchBackend: () => ({ close() {}, on() {} }),
  });

  supervisor.start();

  assert.deepEqual(exits, [1]);
  assert.match(reports[0], /could not start uv: uv is missing/);
});

// Windows has no signals: killing the spawned `uv` left the uvicorn grandchild
// alive on port 3900, so every restart failed to bind and the supervisor tore
// the stack down after three "crashes".
test('a backend is stopped by process tree on windows and by signal elsewhere', () => {
  const child = { pid: 4242, killedWith: null, kill(sig) { this.killedWith = sig; } };
  const calls = [];
  const run = (exe, args) => {
    calls.push([exe, ...args].join(' '));
    return { status: 0 };
  };

  assert.equal(killProcessTree(child, 'SIGTERM', { platform: 'win32', run }), 'taskkill');
  assert.deepEqual(calls, ['taskkill /pid 4242 /T /F']);
  assert.equal(child.killedWith, null, 'must not also signal the direct child');

  assert.equal(killProcessTree(child, 'SIGTERM', { platform: 'linux', run }), 'signal');
  assert.equal(calls.length, 1, 'POSIX must not shell out');
  assert.equal(child.killedWith, 'SIGTERM');
});

test('a failed taskkill falls back to signalling the direct child', () => {
  const child = { pid: 4242, killedWith: null, kill(sig) { this.killedWith = sig; } };
  const run = () => ({ status: 128 });
  assert.equal(killProcessTree(child, 'SIGTERM', { platform: 'win32', run }), 'fallback');
  assert.equal(child.killedWith, 'SIGTERM');
});

test('a forced tree-kill reload restarts instead of reporting a crash', () => {
  const harness = supervisorHarness({ killBackend: () => 'taskkill' });
  harness.supervisor.start();
  harness.watchers[0].onChange('main.py');
  harness.runNextTimer();

  // taskkill /F reports a non-zero exit and no signal — that is the reload we
  // asked for, not a crash.
  harness.children[0].emit('exit', 1, null);

  assert.doesNotMatch(harness.reports.join('\n'), /BACKEND DIED/);
  assert.equal(harness.children.length, 2);
  assert.deepEqual(harness.exits, []);
});
