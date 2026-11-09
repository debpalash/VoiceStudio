import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    const stream = { getTracks: () => [{ stop() {} }] };
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async () => stream,
    });
    window.AudioContext = class {
      sampleRate = 16000;
      state = 'running';
      audioWorklet = { addModule: async () => {} };
      createMediaStreamSource() {
        return { connect() {}, disconnect() {} };
      }
      async close() {}
    };
    window.AudioWorkletNode = class {
      port = { onmessage: null };
      constructor() {
        window.testMicPort = this.port;
      }
      disconnect() {}
    };
  });
  await page.route('**/api/dictation/prefs', (route) =>
    route.fulfill({ json: { enabled: true, model_id: 'sherpa-test', mode: 'toggle' } }),
  );
  await page.route('**/api/dictation/models', (route) =>
    route.fulfill({
      json: {
        engine_available: true,
        models: [{ id: 'sherpa-test', label: 'Test voice', installed: true }],
      },
    }),
  );
  await page.route('**/api/dictation/readiness', (route) =>
    route.fulfill({ json: { ready: true } }),
  );
  let socket;
  let received;
  const frameReceived = new Promise((resolve) => {
    received = resolve;
  });
  await page.routeWebSocket(/\/api\/ws\/transcribe/, (peer) => {
    socket = peer;
    peer.onMessage((message) => {
      if (message === 'EOF') {
        peer.send(
          JSON.stringify({
            type: 'final',
            final_kind: 'utterance',
            text: 'Hello.',
            language: 'en',
          }),
        );
        peer.send(
          JSON.stringify({
            type: 'final',
            final_kind: 'utterance',
            text: 'Hello.',
            language: 'en',
          }),
        );
        peer.send(
          JSON.stringify({
            type: 'final',
            final_kind: 'summary',
            text: 'Hello. Hello.',
            language: 'en',
          }),
        );
      } else received();
    });
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
  await page.goto(base + '/#/transcriptions');
  const worklet = await page.request.get(base + '/aec-worklet.js');
  assert.equal(worklet.status(), 200);
  assert((await worklet.text()).includes('registerProcessor'));
  await page.getByRole('button', { name: 'Start dictation', exact: true }).click();
  await page.getByText('Listening…', { exact: true }).waitFor();
  socket.send(JSON.stringify({ type: 'partial', text: 'Live words' }));
  await page.getByText('Live words', { exact: true }).waitFor();
  await page.evaluate(() => window.testMicPort.onmessage({ data: new Float32Array([0.1, -0.1]) }));
  await frameReceived;
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await page.locator('aside button').filter({ hasText: 'Hello.' }).nth(1).waitFor();
  assert.equal(await page.locator('aside button').filter({ hasText: 'Hello.' }).count(), 2);
  await page.getByRole('button', { name: 'Dictation', exact: true }).click();
  await page.getByText('Listening…', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(await page.locator('aside button').filter({ hasText: 'Hello.' }).count(), 2);
  assert.deepEqual(errors, []);
  console.log('PASS: live dictation controls, partial text, PCM, history commits and cancel');
} finally {
  await browser.close();
}
