import { _electron as electron } from 'playwright';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const executable =
  process.platform === 'win32'
    ? join(workspaceRoot, 'electron/node_modules/electron/dist/electron.exe')
    : resolve(
        workspaceRoot,
        'electron/node_modules/electron/dist',
        process.platform === 'darwin' ? 'Electron.app/Contents/MacOS/Electron' : 'electron',
      );
const backend = process.env.VOICESTUDIO_BACKEND_URL || 'http://127.0.0.1:3900';
const [infoResponse, toolsResponse] = await Promise.all([
  fetch(`${backend}/system/info`),
  fetch(`${backend}/media-tools/status`),
]);
assert.equal(infoResponse.ok, true, 'The live backend must expose its data directory');
assert.equal(toolsResponse.ok, true, 'The live backend must expose media-tool status');
const info = await infoResponse.json();
const tools = await toolsResponse.json();
const ffmpeg = tools?.tools?.ffmpeg?.path;
assert.equal(typeof info.data_dir, 'string');
assert.equal(typeof ffmpeg, 'string');
assert.equal(existsSync(ffmpeg), true, `Installed FFmpeg was not found at ${ffmpeg}`);
const profile = mkdtempSync(join(tmpdir(), 'voicestudio-media-picker-'));

const app = await electron.launch({
  executablePath: executable,
  args: [join(workspaceRoot, 'electron/out/main/index.js'), '--user-data-dir=' + profile],
  env: {
    ...process.env,
    ELECTRON_RENDERER_URL: '',
    VOICESTUDIO_ELECTRON_PROXY_PORT: '0',
    VOICESTUDIO_SKIP_BACKEND: '1',
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
  },
});

const authorizationFiles = [];
const rememberAuthorization = (selection) => {
  const file = join(info.data_dir, '.path-authorizations', `${selection.authorization}.json`);
  authorizationFiles.push(file);
  return file;
};
try {
  const page = await app.firstWindow();
  await page.waitForFunction(() => Boolean(window.voicestudio?.files?.authorizeMediaTool));
  await app.evaluate(({ dialog }, path) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [path] });
  }, ffmpeg);
  const selected = await page.evaluate(() => window.voicestudio.files.authorizeMediaTool('ffmpeg'));
  assert.ok(selected);
  assert.equal(selected.path.toLowerCase(), ffmpeg.toLowerCase());
  assert.match(selected.authorization, /^[a-f0-9]{64}$/);
  const authorizationFile = rememberAuthorization(selected);
  assert.equal(existsSync(authorizationFile), true, 'Picker authorization was not persisted');

  await app.evaluate(({ dialog }) => {
    dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] });
  });
  assert.equal(
    await page.evaluate(() => window.voicestudio.files.authorizeMediaTool('ffprobe')),
    null,
  );

  const modelsPath = join(profile, 'models');
  await app.evaluate(({ dialog }, path) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [path] });
  }, modelsPath);
  const models = await page.evaluate(() => window.voicestudio.files.authorizeModelsDirectory());
  assert.ok(models);
  assert.equal(models.path.toLowerCase(), modelsPath.toLowerCase());
  assert.equal(existsSync(modelsPath), true, 'The selected model directory was not prepared');
  assert.equal(existsSync(rememberAuthorization(models)), true);

  await app.evaluate(({ dialog }) => {
    dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] });
  });
  assert.equal(
    await page.evaluate(() => window.voicestudio.files.authorizeModelsDirectory()),
    null,
  );
  const resetModels = await page.evaluate(() =>
    window.voicestudio.files.authorizeModelsDirectory(true),
  );
  assert.ok(resetModels);
  assert.equal(resetModels.path, '');
  assert.equal(existsSync(rememberAuthorization(resetModels)), true);
  console.log(
    'Native media-tool and model-directory selection, authorization, reset, and cancellation passed.',
  );
} finally {
  for (const file of authorizationFiles) {
    rmSync(file, { force: true });
    assert.equal(existsSync(file), false, `Authorization cleanup failed for ${file}`);
  }
  await app.close();
  const resolvedProfile = resolve(profile);
  assert.equal(dirname(resolvedProfile), resolve(tmpdir()));
  assert.match(basename(resolvedProfile), /^voicestudio-media-picker-/);
  rmSync(resolvedProfile, { recursive: true, force: true });
}
