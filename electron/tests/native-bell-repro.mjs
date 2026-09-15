import { _electron as electron } from 'playwright';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
if (process.platform !== 'darwin')
  throw new Error('Requires macOS and Accessibility permission for native mouse events.');
const scratch = mkdtempSync(join(tmpdir(), 'vs-native-bell-'));
const click = join(scratch, 'native-click');
execFileSync('swiftc', [
  fileURLToPath(new URL('./fixtures/native-click.swift', import.meta.url)),
  '-o',
  click,
]);
const app = await electron.launch({
  args: [fileURLToPath(new URL('./fixtures/native-bell-host.cjs', import.meta.url))],
});
try {
  const page = await app.firstWindow();
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    window.voicestudio = {
      app: {
        platform: 'darwin',
        version: 'test',
        onNavigate: () => () => {},
        onPersistenceFlush: () => () => {},
      },
      repair: {
        list: async () => [],
        getState: async () => ({ status: 'idle', output: '', workspaceAvailable: false }),
        onEvent: () => () => {},
      },
    };
  });
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/clone');
  const bell = page.locator('[data-slot=macos-system-notifications] button');
  await bell.waitFor();
  const rect = await bell.boundingBox();
  const bounds = await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    w.show();
    w.focus();
    return w.getContentBounds();
  });
  await page.waitForTimeout(500);
  execFileSync(click, [
    String(bounds.x + rect.x + rect.width / 2),
    String(bounds.y + rect.y + rect.height / 2),
  ]);
  await page.waitForTimeout(500);
  const nativeOpened = await page.locator('[data-slot=popover-content][data-open]').isVisible();
  console.log(JSON.stringify({ nativeOpened }));
  if (!nativeOpened) {
    await bell.click();
    console.log(
      JSON.stringify({
        automatedOpened: await page.locator('[data-slot=popover-content][data-open]').isVisible(),
      }),
    );
    process.exitCode = 1;
  }
} finally {
  await app.close();
  rmSync(scratch, { recursive: true, force: true });
}
