import { chromium } from 'playwright';
import { readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
const fixtureName = 'Gallery verification ' + Date.now();
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
let voiceId;
let profileId;
const profileIds = new Set();
try {
  await page.goto('http://localhost:3912/#/gallery');
  await page.getByRole('button', { name: 'My Imports', exact: true }).click();
  const uploaded = page.waitForResponse(
    (res) => res.url().endsWith('/api/gallery/upload') && res.request().method() === 'POST',
  );
  await page.locator('input[type=file][accept="audio/*,video/*"]').setInputFiles({
    name: fixtureName + '.wav',
    mimeType: 'audio/wav',
    buffer: readFileSync(resolve('backend/assets/samples/dictation/en_conversational.wav')),
  });
  const result = await uploaded;
  assert.equal(result.status(), 200);
  voiceId = (await result.json()).id;
  const card = page
    .locator('article')
    .filter({ has: page.getByRole('heading', { name: fixtureName, exact: true }) });
  await card.waitFor();
  await card.getByRole('button', { name: 'Preview voice', exact: true }).click();
  await card.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await card.getByRole('button', { name: 'Pause', exact: true }).click();
  const saved = page.waitForResponse(
    (res) => res.url().includes('/save-as-profile?') && res.request().method() === 'POST',
  );
  await card.getByRole('button', { name: 'Use voice', exact: true }).click();
  const saveResponse = await saved;
  assert.equal(saveResponse.status(), 200);
  profileId = (await saveResponse.json()).profile_id;
  profileIds.add(profileId);
  await page.waitForURL('**/#/clone');
  await page.getByRole('button').filter({ hasText: fixtureName }).first().waitFor();
  const out = mkdtempSync(join(tmpdir(), 'voicestudio-gallery-check-'));
  await page.screenshot({ path: join(out, 'imported-voice.png') });
  for (const [target, label] of [
    ['stories', 'Use in Stories'],
    ['audiobook', 'Set as Audiobook default'],
  ]) {
    await page.evaluate(async (target) => {
      const session = await import('/src/features/longform/longform-session.ts');
      session.editLongform(target, {
        script: 'Preserved handoff script',
        title: 'Preserved title',
      });
      window.location.hash = '/gallery';
    }, target);
    await page.getByRole('button', { name: 'My Imports', exact: true }).click();
    const imported = page
      .locator('article')
      .filter({ has: page.getByRole('heading', { name: fixtureName, exact: true }) });
    await imported.getByRole('button', { name: 'More actions', exact: true }).click();
    const materialized = page.waitForResponse(
      (res) => res.url().includes('/save-as-profile?') && res.request().method() === 'POST',
    );
    await page.getByRole('menuitem', { name: label, exact: true }).click();
    const response = await materialized;
    assert.equal(response.status(), 200);
    const id = (await response.json()).profile_id;
    profileIds.add(id);
    await page.waitForURL('**/#/' + target);
    const draft = await page.evaluate(
      async (target) =>
        (await import('/src/features/longform/longform-session.ts')).longformSession.state.drafts[
          target
        ],
      target,
    );
    assert.equal(draft.script, 'Preserved handoff script');
    assert.equal(draft.title, 'Preserved title');
    if (target === 'stories') assert.ok(draft.cast.some((member) => member.profileId === id));
    else assert.equal(draft.voice, id);
  }
  console.log(
    'Real gallery upload, Vidstack preview, clone, Stories and Audiobook handoffs passed.',
  );
} finally {
  for (const profileId of profileIds)
    await page.request.delete('http://127.0.0.1:3900/profiles/' + encodeURIComponent(profileId));
  if (voiceId)
    await page.request.delete(
      'http://127.0.0.1:3900/gallery/voices/' + encodeURIComponent(voiceId),
    );
  await browser.close();
}
