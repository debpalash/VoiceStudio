import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
let probes = 0;
try {
  page.on('request', (req) => {
    if (req.url().endsWith('/setup/preflight')) probes++;
  });
  await page.goto('http://localhost:3912/#/settings/performance');
  const button = page.getByRole('button', { name: 'System check', exact: true });
  await button.waitFor();
  assert.equal(probes, 0);
  const response = page.waitForResponse((res) => res.url().endsWith('/setup/preflight'), {
    timeout: 60000,
  });
  await button.click();
  const result = await response;
  assert.equal(result.status(), 200);
  const report = await result.json();
  assert.ok(report.checks.length >= 4);
  await page.getByRole('heading', { name: report.checks[0].label, exact: true }).waitFor();
  for (const check of report.checks)
    assert.ok(await page.getByRole('heading', { name: check.label, exact: true }).isVisible());
  assert.equal(probes, 1);
  console.log(
    'Live preflight report rendered ' + report.checks.length + ' checks after explicit request.',
  );
} finally {
  await browser.close();
}
