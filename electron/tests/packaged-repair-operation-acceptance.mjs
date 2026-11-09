import { _electron as electron } from 'playwright';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const agentId = process.argv[2];
if (!['codex', 'claude', 'opencode', 'pi'].includes(agentId)) {
  throw new Error(
    'Usage: node electron/tests/packaged-repair-operation-acceptance.mjs <codex|claude|opencode|pi>',
  );
}

const profile = mkdtempSync(join(tmpdir(), 'voicestudio-agent-acceptance-'));
const defaultExecutable =
  process.platform === 'win32'
    ? resolve('electron/release/win-unpacked/VoiceStudio.exe')
    : process.platform === 'darwin'
      ? resolve('electron/release/mac/VoiceStudio.app/Contents/MacOS/VoiceStudio')
      : resolve('electron/release/linux-unpacked/voicestudio-desktop');
const executablePath = process.env.VOICESTUDIO_PACKAGED_EXE
  ? resolve(process.env.VOICESTUDIO_PACKAGED_EXE)
  : defaultExecutable;
assert(existsSync(executablePath), `Packaged executable does not exist: ${executablePath}`);
const launchArgs = [`--user-data-dir=${profile}`];
if (process.env.VOICESTUDIO_TEST_WAYLAND === '1') {
  launchArgs.push('--no-sandbox', '--ozone-platform=wayland');
}
const app = await electron.launch({
  executablePath,
  args: launchArgs,
  env: {
    ...process.env,
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
    VOICESTUDIO_SKIP_BACKEND: '1',
    OMNIVOICE_PORT: process.env.OMNIVOICE_PORT || '3900',
  },
});

try {
  const page = await app.firstWindow();
  await page.waitForFunction(() => Boolean(window.voicestudio?.repair));
  const agents = await page.evaluate(() => window.voicestudio.repair.list());
  const selectedAgent = agents.find((agent) => agent.id === agentId);
  if (process.env.VOICESTUDIO_EXPECT_AGENT_UNAVAILABLE === '1') {
    assert.equal(
      selectedAgent?.available,
      false,
      `${agentId} must not be exposed through an incompatible host-OS shim`,
    );
    console.log(`PASS: packaged ${process.platform} rejects incompatible ${agentId} shim`);
    process.exitCode = 0;
  } else {
    assert.equal(selectedAgent?.available, true, `${agentId} CLI must be detected`);
    const started = await page.evaluate(
      (agent) =>
        window.voicestudio.repair.start({
          agent,
          mode: 'fix',
          report:
            'ACTION_REQUEST: Inspect live VoiceStudio state through the scoped API bridge. Verify that an installed compatible TTS engine is selected and ready. Do not download anything, change settings, or edit files. If it is ready, report the exact selected engine and model.',
          context: JSON.stringify({ route: '/clone', acceptance: true }),
        }),
      agentId,
    );
    const deadline = Date.now() + 180_000;
    let state;
    while (Date.now() < deadline) {
      state = await page.evaluate(() => window.voicestudio.repair.getState());
      if (['complete', 'failed', 'stopped'].includes(state.status)) break;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1_000));
    }
    assert.equal(state?.sessionId, started.sessionId);
    assert.equal(state?.status, 'complete', state?.output || 'Agent did not complete');
    assert.match(state.output, /OmniVoice|k2-fsa/i);
    console.log(state.output.trim());
    console.log(`PASS: packaged ${agentId} app-operation acceptance`);
  }
} finally {
  await app.close();
  rmSync(profile, { recursive: true, force: true });
}
