// Run after `bun run build`: exercises real Chromium views with local fixtures only.
import { _electron as electron } from 'playwright';
import { build } from 'vite';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const scratch = mkdtempSync(join(tmpdir(), 'voicestudio-site-browser-'));
const server = createServer((req, res) => {
  res.setHeader('Content-Type', 'text/html');
  res.end(
    `<title>${req.url}</title><h1>${req.url}</h1><a href="/two" target="_blank">Next page</a>`,
  );
});
let app;
try {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  mkdirSync(join(scratch, 'main'));
  mkdirSync(join(scratch, 'preload'));
  copyFileSync(resolve('out/preload/index.mjs'), join(scratch, 'preload/index.mjs'));
  await build({
    configFile: false,
    build: {
      ssr: resolve('src/main/site-browser.ts'),
      outDir: join(scratch, 'main'),
      emptyOutDir: false,
      rolldownOptions: {
        external: ['electron'],
        output: { format: 'es', entryFileNames: 'site-browser.mjs' },
      },
    },
  });
  const harness = join(scratch, 'main/harness.mjs');
  writeFileSync(
    harness,
    `
    import { app, BrowserWindow, protocol, net } from 'electron';
    import { join } from 'node:path';
    import { pathToFileURL } from 'node:url';
    import { registerSiteBrowser } from './site-browser.mjs';
    app.setPath('userData', ${JSON.stringify(join(scratch, 'profile'))});
    protocol.registerSchemesAsPrivileged([{ scheme:'app', privileges:{ standard:true, secure:true, supportFetchAPI:true, corsEnabled:true, stream:true } }]);
    app.on('browser-window-created', (_event, window) => { window.show = () => {}; });
    app.whenReady().then(async () => {
    protocol.handle('app', request => {
      const path = new URL(request.url).pathname;
      return net.fetch(pathToFileURL(join(${JSON.stringify(resolve('out/renderer'))}, path)).href);
    });
    const parent = new BrowserWindow({ show:false, webPreferences:{ preload:${JSON.stringify(join(scratch, 'preload/index.mjs'))}, sandbox:false, contextIsolation:true, nodeIntegration:false } });
    registerSiteBrowser(() => parent);
    await parent.loadURL('app://voicestudio/index.html');
    });
  `,
  );
  const env = { ...process.env };
  delete env.ELECTRON_RENDERER_URL;
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({
    executablePath: require('electron'),
    args: [harness],
    env,
    timeout: 15000,
  });
  const parent = await app.firstWindow();
  parent.setDefaultTimeout(10000);
  await parent.waitForLoadState('domcontentloaded');
  await parent.locator('[data-slot="studio-workspace"]').waitFor();
  await parent.evaluate(() => {
    const workspace = document.createElement('main');
    workspace.dataset.slot = 'workspace-content';
    workspace.style.cssText = 'position:fixed;left:220px;top:60px;right:0;bottom:40px';
    const anchor = document.createElement('button');
    anchor.id = 'smoke-anchor';
    anchor.textContent = 'VoiceStudio.sh Open Source';
    anchor.style.cssText = 'position:absolute;left:24px;top:24px;height:32px';
    const draft = document.createElement('input');
    draft.id = 'smoke-draft';
    draft.value = 'Unsaved workspace';
    workspace.append(anchor, draft);
    document.querySelector('[data-slot="studio-workspace"]').append(workspace);
    anchor.focus();
  });
  const originalUrl = parent.url();
  await parent.evaluate((url) => window.voicestudio.browser.open(url), origin + '/one');
  const toolbar = parent;
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  toolbar.setDefaultTimeout(10000);
  toolbar.on('pageerror', (error) => console.error('Toolbar error:', error.message));
  toolbar.on('console', (message) => {
    if (message.type() === 'error') console.error('Toolbar console:', message.text());
  });
  toolbar.on('requestfailed', (request) =>
    console.error('Request failed:', request.url(), request.failure()),
  );
  const address = toolbar.getByRole('textbox', { name: 'Website address' });
  try {
    await address.waitFor();
  } catch (error) {
    console.error('Browser document:', await toolbar.content());
    throw error;
  }
  const state = () => toolbar.evaluate(() => window.voicestudio.browser.state());
  await toolbar.waitForFunction(() =>
    document.querySelector('.site-browser-navigation input')?.value.endsWith('/one'),
  );
  const remote = async (code) =>
    app.evaluate(({ BrowserWindow }, script) => {
      const child = BrowserWindow.getAllWindows()[0];
      return child.contentView.children[0].webContents.executeJavaScript(script);
    }, code);
  assert.equal(await remote('typeof window.voicestudio'), 'undefined');
  assert.equal(await remote('typeof require'), 'undefined');
  await remote("document.querySelector('a').click()");
  await toolbar.waitForFunction(() =>
    document.querySelector('.site-browser-navigation input')?.value.endsWith('/two'),
  );
  await toolbar.getByRole('button', { name: 'Back', exact: true }).click();
  await toolbar.waitForFunction(() =>
    document.querySelector('.site-browser-navigation input')?.value.endsWith('/one'),
  );
  await toolbar.getByRole('button', { name: 'Forward', exact: true }).click();
  await toolbar.waitForFunction(() =>
    document.querySelector('.site-browser-navigation input')?.value.endsWith('/two'),
  );
  await address.fill(origin + '/typed');
  await address.press('Enter');
  await toolbar.waitForFunction(() =>
    document.querySelector('.site-browser-navigation input')?.value.endsWith('/typed'),
  );
  await toolbar.getByRole('button', { name: 'Open in browser', exact: true }).click();
  await toolbar.getByRole('button', { name: 'Default browser', exact: true }).waitFor();
  const installed = await toolbar.evaluate(() => window.voicestudio.browser.installed());
  for (const browser of installed)
    await toolbar.getByRole('button', { name: browser.name, exact: true }).waitFor();
  const bounds = await app.evaluate(({ BrowserWindow }) => {
    const child = BrowserWindow.getAllWindows()[0];
    return child.contentView.children[0].getBounds();
  });
  const toolbarBounds = await toolbar.locator('.site-browser-toolbar').boundingBox();
  assert.ok(
    Math.abs(bounds.y - toolbarBounds.y - toolbarBounds.height) <= 1,
    'native page starts below all browser controls',
  );
  const modal = await toolbar.locator('.site-browser-page').boundingBox();
  const anchor = await toolbar.locator('#smoke-anchor').boundingBox();
  assert.ok(
    modal.x >= 236 && modal.y >= anchor.y + anchor.height,
    'modal stays in the right content area below the clicked item',
  );
  assert.ok(
    bounds.x >= modal.x && bounds.x + bounds.width <= modal.x + modal.width,
    'native view stays inside the modal horizontally',
  );
  assert.ok(
    bounds.y + bounds.height <= modal.y + modal.height,
    'native view stays inside the modal vertically',
  );
  for (const [width, height, zoom] of [
    [1280, 800, 1],
    [960, 600, 1.25],
    [720, 500, 1],
  ]) {
    await app.evaluate(
      ({ BrowserWindow }, size) => {
        const window = BrowserWindow.getAllWindows()[0];
        window.setContentSize(size[0], size[1]);
        window.webContents.setZoomFactor(size[2]);
      },
      [width, height, zoom],
    );
    let fits = false;
    for (let attempt = 0; attempt < 40 && !fits; attempt++) {
      await toolbar.waitForTimeout(50);
      const body = await toolbar.locator('.site-browser-viewport').boundingBox();
      const frame = await toolbar.locator('.site-browser-page').boundingBox();
      const native = await app.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()[0].contentView.children[0].getBounds(),
      );
      fits =
        frame.x >= 236 &&
        (frame.x + frame.width) * zoom <= width &&
        (frame.y + frame.height) * zoom <= height &&
        Math.abs(native.x - body.x * zoom) <= 2 &&
        Math.abs(native.y - body.y * zoom) <= 2 &&
        Math.abs(native.width - body.width * zoom) <= 2 &&
        Math.abs(native.height - body.height * zoom) <= 2;
    }
    assert.ok(fits, `modal and native page fit after resize to ${width}x${height} at ${zoom} zoom`);
  }
  assert.equal((await state()).url, origin + '/typed');
  // Optional owner-triggered live-site check; normal CI remains local-only.
  if (process.env.VOICESTUDIO_BROWSER_LIVE_CHECK === '1') {
    await toolbar.evaluate(() => window.voicestudio.browser.navigate('https://voicestudio.sh/'));
    let live = await state();
    for (let attempt = 0; attempt < 120; attempt++) {
      if (live.error || (!live.loading && live.title.includes('VoiceStudio'))) break;
      await toolbar.waitForTimeout(250);
      live = await state();
    }
    assert.equal(live.loading, false, 'official website finishes loading');
    assert.match(live.title, /VoiceStudio/);
    assert.equal(live.error, false, 'official website loads in the isolated native view');
    assert.equal(await toolbar.getByRole('alert').count(), 0, 'no browser action or bounds errors');
    assert.equal(await remote('location.hostname'), 'voicestudio.sh');
    assert.ok((await remote('document.body.innerText')).length > 100);
    console.log(
      'Live website preview passed:',
      JSON.stringify({
        state: live,
        remoteTitle: await remote('document.title'),
        host: await remote('location.hostname'),
      }),
    );
  }
  await toolbar.getByRole('button', { name: 'Return to the studio', exact: true }).click();
  await toolbar.locator('.site-browser-page').waitFor({ state: 'detached' });
  assert.equal(parent.url(), originalUrl);
  assert.equal(await parent.locator('#smoke-draft').inputValue(), 'Unsaved workspace');
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  assert.equal(
    await app.evaluate(
      ({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].contentView.children.length,
    ),
    0,
  );
  console.log(
    'Electron browser smoke passed: one main window, isolated view, navigation, browser picker, layout, and preserved workspace on return.',
  );
} finally {
  await app?.close();
  await new Promise((resolve) => server.close(resolve));
  // scratch is the exact directory returned by mkdtemp, never a caller-supplied path.
  if (scratch.startsWith(join(tmpdir(), 'voicestudio-site-browser-')))
    rmSync(scratch, { recursive: true, force: true, maxRetries: 3 });
}
