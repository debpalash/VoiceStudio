import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { withApp } from './desktop-harness.mjs';
import { ROUTES } from './fixtures/routes.mjs';
import { STAR_COUNT_URL } from './local-first.mjs';

const bridgeFacts = (page) =>
  page.evaluate(() => ({
    title: document.title,
    platform: window.voicestudio?.app?.platform,
    saveData: typeof window.voicestudio?.files?.saveData,
  }));

test('launch: window, title and bridge match the real OS', async (t) => {
  await withApp(t, {}, async ({ page }) => {
    await page.waitForFunction(() => Boolean(window.voicestudio?.files?.saveData));
    const facts = await bridgeFacts(page);
    assert.match(facts.title, /VoiceStudio/);
    assert.equal(facts.platform, process.platform);
    assert.equal(facts.saveData, 'function');
  });
});

test('guard: a main-process request leaving the machine fails the run', async (t) => {
  await assert.rejects(
    withApp(t, {}, async ({ app }) => {
      await app.evaluate(({ net }) => net.fetch('https://example.com/leak').catch(() => {}));
    }),
    /Requests left the machine/,
  );
});

test('guard: a disallowed request is cancelled, not just recorded', async (t) => {
  let outcome;
  await assert.rejects(
    withApp(t, {}, async ({ app }) => {
      outcome = await app.evaluate(({ net }) =>
        net.fetch('https://example.com/leak').then(
          () => 'sent',
          (error) => String(error),
        ),
      );
    }),
    /Requests left the machine/,
  );
  assert.match(outcome, /ERR_BLOCKED_BY_CLIENT/);
});

test('guard: a main-process star-count request is not exempt', async (t) => {
  // Only the renderer's star badge is stubbed by a Playwright route; main has no such stub.
  let outcome;
  await assert.rejects(
    withApp(t, {}, async ({ app }) => {
      outcome = await app.evaluate(
        ({ net }, url) => net.fetch(url).then(() => 'sent', String),
        STAR_COUNT_URL,
      );
    }),
    /Requests left the machine/,
  );
  assert.match(outcome, /ERR_BLOCKED_BY_CLIENT/);
});

test('guard: a Node-level main-process request fails the run', async (t) => {
  // Node's own fetch (undici) bypasses Chromium's session, so it needs its own hook.
  await assert.rejects(
    withApp(t, {}, async ({ app }) => {
      await app.evaluate(() => fetch('http://leak.invalid/node-fetch').catch(() => {}));
    }),
    /Requests left the machine/,
  );
});

test('guard: a run that observed no requests fails', async (t) => {
  await assert.rejects(
    withApp(t, {}, async ({ app, page }) => {
      await page.waitForFunction(() => Boolean(window.voicestudio?.files?.saveData));
      await app.evaluate(async ({ BrowserWindow, net }) => {
        // Detach the observed log: clearing the live array races late requests.
        globalThis.__requestLog = [];
        // A late app request must not undo the simulated missing hook.
        await net.fetch(BrowserWindow.getAllWindows()[0].webContents.getURL());
      });
    }),
    /observed no app requests/,
  );
});

test('guard: a renderer request leaving the machine fails the run', async (t) => {
  // The app's CSP blocks it; the console error is what the harness reports.
  await assert.rejects(
    withApp(t, {}, async ({ page }) => {
      await page.waitForFunction(() => Boolean(window.voicestudio?.files?.saveData));
      await page.evaluate(() => fetch('https://example.com/leak').catch(() => {}));
    }),
    /Renderer reported errors/,
  );
});

test('first run: setup gate completes to home', async (t) => {
  await withApp(t, { seed: false }, async ({ page }) => {
    await page.getByRole('heading', { name: 'System check', exact: true }).first().waitFor();
    const next = page.getByRole('button', { name: 'Continue', exact: true });
    // System check -> Model packs -> Privacy -> Enter studio
    for (const heading of ['Model packs', 'Privacy', 'Enter studio']) {
      await next.click();
      await page.getByRole('heading', { name: heading, exact: true }).first().waitFor();
      if (heading === 'Privacy') {
        // A fresh profile is asked about the optional account (equal-weight Yes/No, owner rule
        // 2026-10-16) and Next stays disabled until it answers. Main reports availability
        // asynchronously, so wait for the question rather than reading it once.
        const local = page.getByRole('button', { name: 'Keep everything local', exact: true });
        const asked = await local
          .waitFor({ timeout: 5000 })
          .then(() => true)
          .catch(() => false);
        if (asked) {
          const yes = page.getByRole('button', { name: 'Turn on account features', exact: true });
          assert.equal(await yes.isVisible(), true, 'Both choices must carry equal weight');
          await local.click();
          await local.waitFor({ state: 'detached' });
        }
      }
    }
    await page.getByRole('button', { name: 'Enter studio', exact: true }).last().click();
    await page.getByRole('heading', { name: 'Home', exact: true }).waitFor();
    assert.equal(
      await page.evaluate(() => localStorage.getItem('voicestudio.setup.complete.v1')),
      '1',
    );
    assert.match(await page.evaluate(() => location.hash), /^(#\/?)?$/);
  });
});

test('consent: declining analytics keeps the app usable and sends nothing', async (t) => {
  const prompted = { available: true, prompted: false, opted_in: false };
  await withApp(
    t,
    { fixtures: { '/api/settings/analytics': prompted } },
    async ({ page, backend }) => {
      const yes = page.getByRole('button', { name: 'Yes, share anonymous stats', exact: true });
      const no = page.getByRole('button', { name: 'No thanks', exact: true });
      await yes.waitFor();
      assert.equal(await no.isVisible(), true, 'Both choices must carry equal weight');
      await no.click();
      await yes.waitFor({ state: 'detached' });
      const writes = backend.requests.filter(
        (r) => r.method === 'PUT' && r.path === '/api/settings/analytics',
      );
      assert.deepEqual(
        writes.map((r) => JSON.parse(r.body)),
        [{ enabled: false }],
      );
      await page.getByRole('heading', { name: 'Home', exact: true }).waitFor();
      // withApp's request guard fails the run if any analytics host was contacted.
    },
  );
});

test('backend down: app shows the connecting state without crashing', async (t) => {
  await withApp(t, { backend: 'down' }, async ({ page }) => {
    await page.getByText('Connecting to the running engine…').waitFor();
    assert.equal(await page.evaluate(() => typeof window.voicestudio?.files?.saveData), 'function');
  });
});

test('routes: every route renders', async (t) => {
  await withApp(t, {}, async ({ page, backend }) => {
    const crashed = [];
    for (const route of ROUTES) {
      await page.evaluate((path) => {
        location.hash = '#' + path;
      }, route);
      // A route may redirect (e.g. /settings -> /settings/general), so accept any hash that
      // still sits under the requested path.
      await page
        .waitForFunction((path) => location.hash.startsWith('#' + path), route, { timeout: 5000 })
        .catch(async () => {
          assert.fail(
            `Route ${route} settled on hash "${await page.evaluate(() => location.hash)}"`,
          );
        });
      // A crashed route replaces the shell with the error boundary's alert.
      await page.locator('main, [role=alert]').first().waitFor();
      if (await page.getByText('This tab hit a snag.', { exact: true }).count()) {
        crashed.push(route);
        // The boundary outlives route changes; reload so later routes are judged on their own.
        await page.reload();
      }
    }
    assert.deepEqual(crashed, [], 'Routes rendered the error boundary');
    const missing = [
      ...new Set(
        backend.requests.filter((r) => r.status === 404).map((r) => `${r.method} ${r.path}`),
      ),
    ];
    assert.deepEqual(missing, [], 'Routes requested backend paths the fake backend lacks');
  });
});

// Symlink-resolved, NFC-normalised, case-folded on Windows: temp dirs are symlinks or 8.3 short
// names on macOS and Windows runners, and the app reports the resolved form.
const canonical = (path) => {
  const real = realpathSync.native(path).normalize('NFC');
  return process.platform === 'win32' ? real.toLowerCase() : real;
};
const insideProfile = (path, profile) => canonical(path).startsWith(canonical(profile));

test('settings: theme and language survive restart, user data stays in the profile', async (t) => {
  await withApp(t, {}, async (session) => {
    const { page, profile } = session;
    await page.evaluate(() => {
      location.hash = '#/settings/appearance';
    });
    const dark = page.getByRole('group', { name: 'Dark', exact: true });
    // Studio is the default palette, so pick the other one to make the change observable.
    const original = dark.getByRole('button', { name: 'VoiceStudio Original', exact: true });
    await original.click();
    await original.and(page.locator('[aria-pressed="true"]')).waitFor();

    await page.evaluate(() => {
      location.hash = '#/settings/general';
    });
    await page.getByRole('combobox', { name: 'Language', exact: true }).click();
    await page.getByRole('option', { name: 'Deutsch', exact: true }).click();
    await page.waitForFunction(() => localStorage.getItem('voicestudio.locale') === 'de');

    const paths = await session.app.evaluate(({ app }) => [
      app.getPath('userData'),
      app.getPath('sessionData'),
    ]);
    for (const path of paths) {
      assert.equal(insideProfile(path, profile), true, `${path} is outside ${profile}`);
    }

    const restarted = await session.relaunch();
    const saved = await restarted.page.evaluate(() => ({
      locale: localStorage.getItem('voicestudio.locale'),
      theme: JSON.parse(localStorage.getItem('voicestudio.theme.v2') || '{}'),
    }));
    assert.equal(saved.locale, 'de');
    assert.equal(saved.theme.dark, 'signal');
    await restarted.page.waitForFunction(
      () => document.documentElement.dataset.themeId === 'signal',
    );
  });
});

test('guard: an unexpected desktop exit fails the run', async (t) => {
  await assert.rejects(
    withApp(t, {}, async ({ app }) => {
      await app.close();
    }),
    /Desktop process exited unexpectedly/,
  );
});

test('guard: requests before an intentional crash still fail after relaunch', async (t) => {
  await assert.rejects(
    withApp(t, {}, async (session) => {
      await session.app.evaluate(({ net }) => net.fetch('https://example.com/leak').catch(() => {}));
      await session.crash();
      await session.relaunch();
    }),
    /Requests left the machine/,
  );
});

test('unclean exit: relaunch on the same profile starts normally', async (t) => {
  await withApp(t, {}, async (session) => {
    await session.crash();
    const restarted = await session.relaunch();
    await restarted.page.waitForFunction(() => Boolean(window.voicestudio?.files?.saveData));
    const facts = await bridgeFacts(restarted.page);
    assert.equal(facts.platform, process.platform);
  });
});

test('dialogs: open and save round-trip through the main process', async (t) => {
  await withApp(t, {}, async ({ app, page, profile }) => {
    await page.waitForFunction(() => Boolean(window.voicestudio?.files?.saveData));
    const stub = (method, result) =>
      app.evaluate(
        ({ dialog }, [name, value]) => {
          dialog[name] = async () => value;
        },
        [method, result],
      );

    // Open: a chosen directory comes back authorized; a cancel comes back as null.
    const models = join(profile, 'models');
    await stub('showOpenDialog', { canceled: false, filePaths: [models] });
    const picked = await page.evaluate(() => window.voicestudio.files.authorizeModelsDirectory());
    assert.equal(canonical(picked.path), canonical(models));
    assert.match(picked.authorization, /^[a-f0-9]{64}$/);
    await stub('showOpenDialog', { canceled: true, filePaths: [] });
    assert.equal(
      await page.evaluate(() => window.voicestudio.files.authorizeModelsDirectory()),
      null,
    );

    // Save: the bytes land at the chosen path; a cancel writes nothing.
    const output = join(profile, 'out', 'dialog-test.txt');
    mkdirSync(dirname(output), { recursive: true });
    await stub('showSaveDialog', { canceled: false, filePath: output });
    const saved = await page.evaluate(() =>
      window.voicestudio.files.saveData({
        data: new TextEncoder().encode('VoiceStudio desktop test'),
        suggestedName: 'dialog-test.txt',
      }),
    );
    assert.deepEqual(saved, { canceled: false, path: output });
    assert.equal(readFileSync(output, 'utf8'), 'VoiceStudio desktop test');

    rmSync(output);
    await stub('showSaveDialog', { canceled: true });
    const cancelled = await page.evaluate(() =>
      window.voicestudio.files.saveData({
        data: new TextEncoder().encode('never written'),
        suggestedName: 'dialog-test.txt',
      }),
    );
    assert.deepEqual(cancelled, { canceled: true });
    assert.equal(existsSync(output), false);
  });
});

test('harness: seeding survives a document without storage access', async (t) => {
  // The seed script runs on every document, including ones where localStorage throws
  // (data: URLs, the blank-window fallback). It must not raise a page error there.
  await withApp(t, {}, async ({ page }) => {
    await page.goto('data:text/html,<p>no storage here</p>');
    await page.getByText('no storage here').waitFor();
  });
});

test('reload keeps the bridge working', async (t) => {
  await withApp(t, {}, async ({ page }) => {
    await page.reload();
    await page.waitForFunction(() => Boolean(window.voicestudio?.files?.saveData));
    const facts = await bridgeFacts(page);
    assert.equal(facts.platform, process.platform);
    assert.equal(facts.saveData, 'function');
  });
});
