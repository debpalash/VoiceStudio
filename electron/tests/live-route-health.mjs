/**
 * Live renderer acceptance against the running development app and backend.
 *
 * Start Electron with `bun run dev -- --remote-debugging-port=9333`, then run
 * `bun run smoke:live-routes`. This deliberately follows hash navigation: a
 * history.pushState sweep does not exercise TanStack Router's hash history.
 */
const routes = [
  '/',
  '/clone',
  '/personas',
  '/stories',
  '/dub',
  '/batch',
  '/gallery',
  '/transcriptions',
  '/calls',
  '/design',
  '/audiobook',
  '/projects',
  '/tools',
  '/integrations',
  '/settings/general',
  '/settings/appearance',
  '/settings/models',
  '/settings/models/tts',
  '/settings/models/asr',
  '/settings/models/dictation',
  '/settings/models/diarisation',
  '/settings/models/translation',
  '/settings/models/llm',
  '/settings/media',
  '/settings/pronunciation',
  '/settings/network',
  '/settings/sharing',
  '/settings/credentials',
  '/settings/performance',
  '/settings/usage',
  '/settings/workers',
  '/settings/privacy',
  '/settings/permissions',
  '/settings/storage',
  '/settings/support',
  '/settings/updates',
  '/settings/diagnostics',
  '/settings/logs',
  '/settings/openapi',
];

const cdpBase = process.env.VOICESTUDIO_CDP_URL ?? 'http://127.0.0.1:9333';
const rendererUrl = process.env.VOICESTUDIO_RENDERER_URL ?? 'http://localhost:3902/#/';
const targets = await fetch(`${cdpBase}/json/list`).then((response) => response.json());
const page = targets.find((target) => target.type === 'page' && target.title === 'VoiceStudio');
if (!page) throw new Error('VoiceStudio CDP page was not found');

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let nextId = 0;
const pending = new Map();
const exceptions = [];
const consoleErrors = [];
const consoleWarnings = [];
const failedResponses = [];
const failedLoads = [];
const visited = [];
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data);
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    return message.error ? reject(new Error(message.error.message)) : resolve(message.result);
  }
  if (message.method === 'Runtime.exceptionThrown') {
    exceptions.push(
      message.params.exceptionDetails.exception?.description ??
        message.params.exceptionDetails.text,
    );
  }
  if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
    consoleErrors.push(
      message.params.args.map((arg) => arg.value ?? arg.description ?? '').join(' '),
    );
  }
  if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'warning') {
    consoleWarnings.push(
      message.params.args.map((arg) => arg.value ?? arg.description ?? '').join(' '),
    );
  }
  if (message.method === 'Network.responseReceived') {
    const { response } = message.params;
    const path = new URL(response.url).pathname;
    // Calls feature-detects its API: a 404 there is the "update the backend" state.
    const callsFallback =
      response.status === 404 && (path === '/api/calls' || path.startsWith('/api/calls/'));
    if (response.status >= 400 && response.url.includes('/api/') && !callsFallback) {
      failedResponses.push({ status: response.status, url: path });
    }
  }
  if (message.method === 'Network.loadingFailed' && !message.params.canceled) {
    failedLoads.push(message.params.errorText);
  }
});

function send(method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
await send('Runtime.enable');
await send('Network.enable');
await send('Page.navigate', { url: rendererUrl });
await wait(1_000);

for (const route of routes) {
  await send('Runtime.evaluate', { expression: `location.hash = ${JSON.stringify(`#${route}`)}` });
  await wait(route === '/settings/openapi' ? 800 : 400);
  const result = await send('Runtime.evaluate', {
    expression: `JSON.stringify({ hash: location.hash, title: document.querySelector('h1,h2')?.textContent?.trim() ?? '', error: document.body.innerText.includes('This tab hit a snag') })`,
    returnByValue: true,
  });
  visited.push({ route, ...JSON.parse(result.result.value) });
}

await wait(1_000);
// Leave the running app at its neutral launch view instead of the final route.
await send('Page.navigate', { url: rendererUrl });
await wait(400);
socket.close();

const brokenRoutes = visited.filter(
  ({ route, hash, title, error }) => hash !== `#${route}` || !title || error,
);
const failed =
  visited.length !== routes.length ||
  brokenRoutes.length ||
  exceptions.length ||
  consoleErrors.length ||
  consoleWarnings.length ||
  failedResponses.length ||
  failedLoads.length;
if (failed) {
  console.error(
    JSON.stringify(
      {
        visited,
        brokenRoutes,
        exceptions,
        consoleErrors,
        consoleWarnings,
        failedResponses,
        failedLoads,
      },
      null,
      2,
    ),
  );
  process.exitCode = 1;
} else {
  console.log(
    `Live route health: ${visited.length} routes passed with no renderer warnings, errors or API failures.`,
  );
}
