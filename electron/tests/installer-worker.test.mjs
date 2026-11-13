import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../../', import.meta.url);
const shell = await readFile(new URL('scripts/install.sh', root), 'utf8');
const powershell = await readFile(new URL('scripts/install.ps1', root), 'utf8');
const source = (await readFile(new URL('deploy/install-worker/worker.mjs', root), 'utf8'))
  .replace("import shell from '../../scripts/install.sh';", () => `const shell = ${JSON.stringify(shell)};`)
  .replace("import powershell from '../../scripts/install.ps1';", () => `const powershell = ${JSON.stringify(powershell)};`);
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

for (const domain of ['voicestudio.sh', 'www.voicestudio.sh']) {
  test(`${domain} serves the exact bundled shell script`, async () => {
    const response = worker.fetch(new Request(`https://${domain}/install`, { headers: { 'User-Agent': 'curl/8' } }));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.equal(await response.text(), shell);
  });
}
test('PowerShell user agent and explicit suffix choose the Windows installer', async () => {
  for (const path of ['/install', '/install.ps1']) {
    const response = worker.fetch(new Request(`https://voicestudio.sh${path}`, { headers: { 'User-Agent': 'PowerShell/7' } }));
    assert.equal(await response.text(), powershell);
  }
});
test('explicit shell suffix wins over user agent', async () => {
  assert.equal(await worker.fetch(new Request('https://voicestudio.sh/install.sh', { headers: { 'User-Agent': 'PowerShell/7' } })).text(), shell);
});
test('HTML visitors see all three installation choices', async () => {
  const response = worker.fetch(new Request('https://voicestudio.sh/install', { headers: { Accept: 'text/html' } }));
  assert.match(response.headers.get('Content-Type'), /text\/html/);
  const html = await response.text();
  assert.match(html, /--version X.Y.Z/);
  assert.match(html, /--main/);
  assert.match(html, /Electron/);
  // The live /install route can serve the shell script to PowerShell (#2973).
  assert.doesNotMatch(html, /irm https:\/\/voicestudio\.sh\/install \|/);
  assert.match(html, /irm https:\/\/voicestudio\.sh\/install\.ps1 \| iex/);
});
test('HEAD is bodyless and unsupported methods are rejected', async () => {
  assert.equal(await worker.fetch(new Request('https://voicestudio.sh/install', { method: 'HEAD' })).text(), '');
  assert.equal(worker.fetch(new Request('https://voicestudio.sh/install', { method: 'POST' })).status, 405);
});
