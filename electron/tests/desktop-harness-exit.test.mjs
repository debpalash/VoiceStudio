import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { forceAndWaitForExit } from './desktop-harness-exit.mjs';

test('a tree-kill error is harmless only after both wrapper and Electron stop', async () => {
  const child = new EventEmitter(), app = new EventEmitter();
  await forceAndWaitForExit(child, app, () => {
    queueMicrotask(() => { child.emit('exit'); app.emit('close'); });
    throw new Error('taskkill raced an exiting renderer');
  }, new AbortController().signal);
});

for (const missing of ['wrapper', 'app']) {
  test(`a tree-kill error still fails when the ${missing} remains alive`, async () => {
    const child = new EventEmitter(), app = new EventEmitter();
    const controller = new AbortController();
    const failure = new Error('taskkill could not terminate the process');
    await assert.rejects(forceAndWaitForExit(child, app, () => {
      queueMicrotask(() => {
        if (missing !== 'wrapper') child.emit('exit');
        if (missing !== 'app') app.emit('close');
        controller.abort();
      });
      throw failure;
    }, controller.signal), (error) => error === failure);
  });
}

test('a successful command still waits for both actual exit events', async () => {
  const child = new EventEmitter(), app = new EventEmitter();
  const controller = new AbortController();
  await assert.rejects(forceAndWaitForExit(child, app, () => {
    queueMicrotask(() => { child.emit('exit'); controller.abort(); });
  }, controller.signal), { name: 'AbortError' });
});
