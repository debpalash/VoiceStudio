import { _electron as electron } from 'playwright';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const targetArch = process.env.VOICESTUDIO_RUST_TARGET?.startsWith('aarch64')
  ? 'arm64'
  : process.env.VOICESTUDIO_RUST_TARGET?.startsWith('x86_64')
    ? 'x64'
    : process.arch;
const defaultExecutableCandidates =
  process.platform === 'win32'
    ? targetArch === 'arm64'
      ? [
          'electron/release/win-arm64-unpacked/VoiceStudio.exe',
          'electron/release/win-unpacked/VoiceStudio.exe',
        ]
      : ['electron/release/win-unpacked/VoiceStudio.exe']
    : process.platform === 'darwin'
      ? targetArch === 'arm64'
        ? [
            'electron/release/mac-arm64/VoiceStudio.app/Contents/MacOS/VoiceStudio',
            'electron/release/mac/VoiceStudio.app/Contents/MacOS/VoiceStudio',
          ]
        : [
            'electron/release/mac/VoiceStudio.app/Contents/MacOS/VoiceStudio',
            'electron/release/mac-x64/VoiceStudio.app/Contents/MacOS/VoiceStudio',
          ]
      : targetArch === 'arm64'
        ? [
            'electron/release/linux-arm64-unpacked/voicestudio-electron',
            'electron/release/linux-unpacked/voicestudio-electron',
          ]
        : ['electron/release/linux-unpacked/voicestudio-electron'];
const defaultExecutable =
  defaultExecutableCandidates.find((candidate) => existsSync(resolve(repoRoot, candidate))) ??
  defaultExecutableCandidates[0];
const executablePath = resolve(repoRoot, process.env.VOICESTUDIO_PACKAGED_EXE || defaultExecutable);
const existing = process.env.VOICESTUDIO_TEST_PROFILE;
const profile = existing || mkdtempSync(join(tmpdir(), 'voicestudio-packaged-check-'));
const keepProfile = process.env.VOICESTUDIO_KEEP_TEST_PROFILE === '1';
const setup = process.argv.includes('--setup');
const firstSound = process.argv.includes('--first-sound');
const quitDuringStartup = process.argv.includes('--quit-during-startup');
const install = process.argv.includes('--install') || firstSound;
const wayland = process.platform === 'linux' && process.env.VOICESTUDIO_TEST_WAYLAND === '1';
const scaleFactor = Number(process.env.VOICESTUDIO_TEST_SCALE_FACTOR);
const extractAndRun = process.env.APPIMAGE_EXTRACT_AND_RUN === '1';
if (quitDuringStartup && !existing)
  throw new Error('--quit-during-startup requires VOICESTUDIO_TEST_PROFILE with a managed runtime');
const activate = (locator) =>
  extractAndRun || wayland ? locator.dispatchEvent('click') : locator.click();
let env = { ...process.env };
let verifyManagedShutdown = false;
if (setup || install || existing) {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  env = {
    ...env,
    OMNIVOICE_PORT: String(port),
    OMNIVOICE_BACKEND_CMD: '',
    VOICESTUDIO_SKIP_BACKEND: '',
    HF_HOME: join(profile, 'hf-home'),
    HF_HUB_CACHE: join(profile, 'hf-home', 'hub'),
    HF_HUB_OFFLINE: firstSound ? '0' : '1',
    TRANSFORMERS_OFFLINE: firstSound ? '0' : '1',
    UV_CACHE_DIR: join(profile, 'uv-cache'),
    OMNIVOICE_DATA_DIR: join(profile, 'data'),
    OMNIVOICE_PRELOAD_TTS_ASR: '0',
    OMNIVOICE_PRELOAD_CAPTURE_ASR: '0',
    OMNIVOICE_PRELOAD_WATERMARK: '0',
  };
}
const app = await electron.launch({
  executablePath,
  args: [
    '--user-data-dir=' + profile,
    ...(wayland ? ['--ozone-platform=wayland'] : []),
    ...(Number.isFinite(scaleFactor) && scaleFactor > 0
      ? [`--force-device-scale-factor=${scaleFactor}`]
      : []),
  ],
  env,
  timeout: 30000,
});
try {
  const native = await app.evaluate(async () => {
    const { spawn } = process.getBuiltinModule('child_process');
    const { join } = process.getBuiltinModule('path');
    return new Promise((resolve, reject) => {
      const executable = join(
        process.resourcesPath,
        'native',
        'voicestudio-desktop-bridge' + (process.platform === 'win32' ? '.exe' : ''),
      );
      const child = spawn(executable, [String(process.pid)], {
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      let output = '';
      let errors = '';
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error('Packaged helper timeout: ' + errors));
      }, 5000);
      child.stdout.on('data', (chunk) => {
        output += chunk.toString();
      });
      child.stderr.on('data', (chunk) => {
        errors = (errors + chunk.toString()).slice(-8192);
      });
      child.once('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once('close', (code) => {
        clearTimeout(timer);
        try {
          if (code !== 0) throw new Error('Packaged helper failed: ' + code + ': ' + errors);
          resolve(JSON.parse(output));
        } catch (error) {
          reject(error);
        }
      });
      child.stdin.end(JSON.stringify({ id: 1, method: 'ping' }) + '\n');
    });
  });
  assert.deepEqual(native, { id: 1, result: { protocol: 1 } });
  const window = await app.firstWindow();
  const errors = [];
  window.on('pageerror', (error) => errors.push(error.message));
  await window.waitForURL('app://voicestudio/**');
  if (firstSound) {
    const resumed = await window.evaluate(() => {
      const complete = localStorage.getItem('voicestudio.setup.complete.v1') === '1';
      localStorage.removeItem('voicestudio.setup.complete.v1');
      localStorage.removeItem('voicestudio.setup.in-progress.v1');
      return complete;
    });
    if (resumed) await window.reload();
  }

  if (quitDuringStartup) {
    const sentinel = join(profile, 'data', 'run_sentinel.json');
    await assertEventually(
      () => existsSync(sentinel),
      10_000,
      'Backend did not create its run sentinel during startup',
    );
    const progress = await fetch(
      `http://127.0.0.1:${env.OMNIVOICE_PORT}/startup/progress?ts=${Date.now()}`,
      { cache: 'no-store' },
    ).then((response) => response.json());
    assert.equal(
      progress.status,
      'starting',
      'Acceptance must quit before deferred startup is ready',
    );
    verifyManagedShutdown = true;
    console.log('PASS: packaged backend reached deferred startup before deliberate quit');
  } else if (setup) {
    await window.getByText('VoiceStudio', { exact: true }).first().waitFor();
    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.setSize(960, 640);
    });
    const setupLayout = await window.evaluate(() => {
      const header = document.querySelector('header.workspace-titlebar');
      const scroller = document.querySelector('[data-testid="backend-gate-scroll"]');
      const brand = header?.textContent?.includes('VoiceStudio') ? header : null;
      const headerRect = header?.getBoundingClientRect();
      const scrollRect = scroller?.getBoundingClientRect();
      return {
        brandVisible: Boolean(brand && headerRect && headerRect.height > 0),
        headerTop: headerRect?.top,
        headerBottom: headerRect?.bottom,
        scrollTop: scrollRect?.top,
        viewportHeight: document.documentElement.clientHeight,
        width: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      };
    });
    assert.equal(setupLayout.brandVisible, true, 'Setup branding must remain visible');
    assert.equal(setupLayout.headerTop, 0, 'Setup header must stay pinned to the window top');
    assert.equal(
      setupLayout.scrollTop,
      setupLayout.headerBottom,
      'Setup content must scroll below the fixed branding',
    );
    assert(
      setupLayout.headerBottom <= setupLayout.viewportHeight,
      'Setup branding must remain inside the short-window viewport',
    );
    assert(
      setupLayout.scrollWidth <= setupLayout.width + 1,
      'Setup gate must not overflow horizontally',
    );
    const setupStatus = await waitForRuntimeGate(window, 30_000);
    const installButton = window.getByRole('button', { name: 'Install local runtime', exact: true });
    if (process.platform === 'darwin' && targetArch === 'x64') {
      assert.equal(setupStatus.setupIssue, 'unsupported_platform');
      await window.getByRole('textbox', { name: 'Backend URL', exact: true }).waitFor();
      await window.getByRole('button', { name: 'Test connection', exact: true }).waitFor();
      assert.equal(await installButton.count(), 0, 'Intel Macs must offer remote setup');
    } else {
      await installButton.waitFor();
    }
    assert.equal(
      await window.evaluate(() => typeof window.voicestudio.backend.cleanSetupRuntime),
      'function',
    );
    const stage = await window.evaluate(
      async () => (await window.voicestudio.backend.getStatus()).stage,
    );
    assert.equal(stage, 'setup_required');
    assert.equal(
      existsSync(join(profile, 'runtime')),
      false,
      'Startup must not install before the explicit action',
    );
    const customParent = join(profile, 'custom-runtime-parent');
    await app.evaluate(({ dialog }, path) => {
      dialog.showOpenDialog = async () => ({
        canceled: false,
        filePaths: [path],
      });
    }, customParent);
    const selected = await window.evaluate(() =>
      window.voicestudio.backend.chooseRuntimeLocation('Choose runtime location'),
    );
    assert.deepEqual(selected, {
      path: join(customParent, 'VoiceStudio'),
      custom: true,
    });
    assert.equal(
      (await window.evaluate(() => window.voicestudio.backend.getStatus())).runtimeCustom,
      true,
    );
    const restored = await window.evaluate(() =>
      window.voicestudio.backend.useDefaultRuntimeLocation(),
    );
    assert.equal(restored.custom, false);
    assert.equal(
      (await window.evaluate(() => window.voicestudio.backend.getStatus())).runtimeCustom,
      false,
    );
    await app.evaluate(({ dialog }) => {
      dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] });
    });
    assert.equal(
      await window.evaluate(() =>
        window.voicestudio.backend.chooseRuntimeLocation('Choose runtime location'),
      ),
      null,
    );
    assert.equal(
      existsSync(join(customParent, 'VoiceStudio')),
      false,
      'Choosing a runtime location must not install before consent',
    );
    assert.deepEqual(errors, []);
    console.log(
      'PASS: packaged first-run setup awaits explicit action and its native location picker changes/restores the destination without installing',
    );
  } else {
    if (install) {
      console.log('Testing explicit installation in', profile);
      let state = await waitForRuntimeGate(window, firstSound ? 120_000 : 30_000);
      if (state.stage !== 'ready') {
        const installButton = window.getByRole('button', {
          name: 'Install local runtime',
          exact: true,
        });
        // WSLg/Wayland can report a stable DOM box while Playwright's native
        // compositor actionability check never settles. Pointer behavior is
        // covered separately; this flow verifies setup and first sound.
        await activate(installButton);
      }
      const deadline =
        Date.now() +
        (Number(process.env.VOICESTUDIO_RUNTIME_TIMEOUT_MS) || (firstSound ? 1_800_000 : 600_000));
      let lastStage = '';
      for (;;) {
        state = await window.evaluate(async () => window.voicestudio.backend.getStatus());
        if (state.stage !== lastStage) {
          console.log('Runtime:', state.stage);
          lastStage = state.stage;
        }
        if (state.stage === 'ready') break;
        if (['setup_required', 'failed', 'crashed', 'port_in_use'].includes(state.stage))
          throw new Error(JSON.stringify(state));
        assert(Date.now() < deadline, 'Runtime startup timed out');
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      assert.equal(
        await window.evaluate(async () => (await window.voicestudio.backend.getStatus()).managed),
        true,
      );
    }
    if (firstSound) {
      const continueButton = window.getByRole('button', {
        name: 'All good — continue',
        exact: true,
      });
      const systemCheck = window.getByRole('button', {
        name: /^(System check|Re-check)$/,
      });
      await systemCheck.waitFor({ timeout: 120_000 });
      await activate(systemCheck);
      await continueButton.waitFor({ state: 'visible' });
      await assertEventually(
        async () => continueButton.isEnabled(),
        120_000,
        'System preflight did not enable onboarding',
      );
      await activate(continueButton);

      const requiredModel = window
        .locator('#model-k2-fsa-OmniVoice')
        .locator('xpath=ancestor::*[@data-slot="settings-row"][1]');
      await requiredModel.waitFor({ timeout: 120_000 });
      const modelsReady = await window.evaluate(async () => {
        const response = await fetch('/api/setup/status');
        return response.ok && (await response.json()).models_ready === true;
      });
      if (!modelsReady) {
        const download = requiredModel.getByRole('button', {
          name: 'Download',
          exact: true,
        });
        await activate(download);
        await waitForSetupModels(
          window,
          Number(process.env.VOICESTUDIO_MODEL_TIMEOUT_MS) || 3_600_000,
        );
      }
      await assertEventually(
        async () => continueButton.isEnabled(),
        30_000,
        'Required model completed but onboarding remained blocked',
      );
      await activate(continueButton);

      const decline = window.getByRole('button', {
        name: 'No thanks',
        exact: true,
      });
      if (await decline.isVisible().catch(() => false)) await activate(decline);
      await assertEventually(
        async () => continueButton.isEnabled(),
        30_000,
        'Privacy choice did not enable onboarding',
      );
      await activate(continueButton);
      const enter = window.getByRole('button', {
        name: 'Enter studio',
        exact: true,
      });
      await enter.waitFor();
      await activate(enter);

      await window.getByRole('region', { name: 'Latest take', exact: true }).waitFor({
        timeout: Number(process.env.VOICESTUDIO_FIRST_SOUND_TIMEOUT_MS) || 1_800_000,
      });
      const proof = await window.evaluate(async () => {
        const historyResponse = await fetch('/api/history');
        if (!historyResponse.ok) throw new Error(`History returned ${historyResponse.status}`);
        const history = await historyResponse.json();
        const take = Array.isArray(history) ? history[0] : null;
        if (!take?.audio_path) throw new Error('First sound did not create a history artifact');
        const audioResponse = await fetch('/api/audio/' + encodeURIComponent(take.audio_path));
        if (!audioResponse.ok) throw new Error(`Audio returned ${audioResponse.status}`);
        const bytes = new Uint8Array(await audioResponse.arrayBuffer());
        return {
          id: take.id,
          size: bytes.byteLength,
          riff: new TextDecoder('ascii').decode(bytes.slice(0, 4)),
          wave: new TextDecoder('ascii').decode(bytes.slice(8, 12)),
          paused: Array.from(document.querySelectorAll('audio, video')).every(
            (media) => media.paused,
          ),
        };
      });
      assert(proof.id);
      assert(proof.size > 44, 'First sound WAV is empty');
      assert.equal(proof.riff, 'RIFF');
      assert.equal(proof.wave, 'WAVE');
      assert.equal(proof.paused, true, 'Completed first sound must await manual playback');
      console.log(
        `PASS: ${existing ? 'resumed' : 'uncached'} packaged onboarding produced a paused ${proof.size}-byte first-sound WAV`,
      );
    }
    if (!firstSound) {
      await window.getByText('VoiceStudio', { exact: true }).first().waitFor({ timeout: 120_000 });
    }
    if (existing) {
      const deadline = Date.now() + 120_000;
      for (;;) {
        const current = await window.evaluate(async () => window.voicestudio.backend.getStatus());
        if (current.stage === 'ready') break;
        if (['setup_required', 'failed', 'crashed', 'port_in_use'].includes(current.stage))
          throw new Error(JSON.stringify(current));
        assert(Date.now() < deadline, 'Existing packaged runtime startup timed out');
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      assert.equal(
        await window.evaluate(async () => (await window.voicestudio.backend.getStatus()).managed),
        true,
      );
    }
    const state = await window.evaluate(async () => {
      const response = await fetch('/api/system/info');
      return {
        status: response.status,
        version: (await response.json()).app_version,
        bridge: typeof window.voicestudio,
      };
    });
    assert.equal(state.status, 200);
    assert.equal(state.bridge, 'object');
    assert(state.version);
    await window.goto('app://voicestudio/#/settings/models');
    await window.getByRole('textbox', { name: 'Custom mirror URL', exact: true }).waitFor();
    assert.deepEqual(errors, []);
    const layout = await window.evaluate(() => ({
      devicePixelRatio,
      width: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    assert(
      layout.scrollWidth <= layout.width + 1,
      'Packaged renderer must not overflow horizontally',
    );
    if (Number.isFinite(scaleFactor) && scaleFactor > 0)
      assert(
        Math.abs(layout.devicePixelRatio - scaleFactor) < 0.05,
        `Expected device scale ${scaleFactor}, received ${layout.devicePixelRatio}`,
      );
    if (process.env.VOICESTUDIO_SCREENSHOT && !extractAndRun) {
      await window.screenshot({ path: process.env.VOICESTUDIO_SCREENSHOT });
    }
    console.log(
      `PASS: packaged ${process.platform}${wayland ? ' Wayland' : ''} renderer, preload bridge and same-origin live backend connection`,
      state.version,
    );
    verifyManagedShutdown = install || Boolean(existing);
  }
} catch (error) {
  const windows = app.windows();
  if (windows[0])
    console.error(
      await windows[0]
        .evaluate(async () => window.voicestudio.backend.getStatus())
        .catch(() => 'No bridge'),
    );
  throw error;
} finally {
  try {
    await app.close();
    if (verifyManagedShutdown) {
      const sentinel = join(profile, 'data', 'run_sentinel.json');
      const deadline = Date.now() + 5_000;
      while (existsSync(sentinel) && Date.now() < deadline)
        await new Promise((resolve) => setTimeout(resolve, 100));
      assert.equal(
        existsSync(sentinel),
        false,
        'A deliberate desktop shutdown must retire the backend run sentinel',
      );
      console.log('PASS: packaged managed backend shut down cleanly');
    }
  } finally {
    if (!existing && !keepProfile)
      rmSync(profile, { recursive: true, force: true, maxRetries: 4, retryDelay: 250 });
  }
}

async function assertEventually(check, timeout, message) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(message);
}

async function waitForRuntimeGate(window, timeout) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const state = await window.evaluate(async () => window.voicestudio.backend.getStatus());
    if (state.stage === 'ready' || state.stage === 'setup_required') return state;
    if (['failed', 'crashed', 'port_in_use'].includes(state.stage)) {
      throw new Error(JSON.stringify(state));
    }
    if (Date.now() >= deadline) throw new Error('Runtime setup gate timed out');
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

async function waitForSetupModels(window, timeout) {
  const deadline = Date.now() + timeout;
  let last = '';
  while (Date.now() < deadline) {
    const state = await window.evaluate(async () => {
      const [setupResponse, jobsResponse] = await Promise.all([
        fetch('/api/setup/status'),
        fetch('/api/models/install/status'),
      ]);
      return {
        setup: setupResponse.ok ? await setupResponse.json() : null,
        jobs: jobsResponse.ok ? await jobsResponse.json() : null,
      };
    });
    if (state.setup?.models_ready) return;
    const job = state.jobs?.jobs?.find((item) => item.repo_id === 'k2-fsa/OmniVoice');
    const summary = [job?.state, job?.phase, job?.bytes_done, job?.total_bytes]
      .filter((value) => value !== undefined && value !== null)
      .join(' ');
    if (summary && summary !== last) {
      console.log('Model:', summary);
      last = summary;
    }
    if (job?.state === 'failed' || job?.state === 'cancelled') {
      throw new Error(`Required model installation ${job.state}: ${job.error || 'unknown error'}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error('Required model installation timed out');
}
