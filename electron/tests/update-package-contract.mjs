import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { basename, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const electronRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function yamlValue(value) {
  const trimmed = value.trim();
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith("'") && trimmed.endsWith("'")) ||
      (trimmed.startsWith('"') && trimmed.endsWith('"')))
  )
    return trimmed.slice(1, -1);
  return trimmed;
}

function capture(source, pattern, label) {
  const value = source.match(pattern)?.[1];
  assert(value, `Update metadata declares ${label}`);
  return yamlValue(value);
}

async function fileHashes(path, includeSha1) {
  const sha512 = createHash('sha512');
  const sha1 = includeSha1 ? createHash('sha1') : null;
  for await (const chunk of createReadStream(path)) {
    sha512.update(chunk);
    sha1?.update(chunk);
  }
  return { sha512: sha512.digest('base64'), sha1: sha1?.digest('hex') };
}

const platform = option('platform', process.platform);
const arch = option('arch', process.arch);
const channel = option(
  'channel',
  process.env.VOICESTUDIO_UPDATE_CHANNEL || `electron-stable-${platform}-${arch}`,
);
const releaseDir = resolve(electronRoot, option('release', 'release'));

assert(['win32', 'darwin', 'linux'].includes(platform), `Supported platform: ${platform}`);
assert(['x64', 'arm64'].includes(arch), `Supported architecture: ${arch}`);

const priorTestArch = process.env.TEST_UPDATER_ARCH;
process.env.TEST_UPDATER_ARCH = arch;
const { Provider } = require('electron-updater/out/providers/Provider.js');
const runtimeProvider = new Provider({ platform });
const metadataFilename = `${runtimeProvider.getCustomChannelName(channel)}.yml`;
if (priorTestArch === undefined) delete process.env.TEST_UPDATER_ARCH;
else process.env.TEST_UPDATER_ARCH = priorTestArch;
const metadataPath = resolve(releaseDir, metadataFilename);
assert(existsSync(metadataPath), `Update metadata exists: ${basename(metadataPath)}`);

const metadata = readFileSync(metadataPath, 'utf8');
const expectedVersion = JSON.parse(
  readFileSync(resolve(electronRoot, '../frontend/package.json'), 'utf8'),
).version;
const version = capture(metadata, /^version:\s*(.+)$/m, 'version');
const fileUrl = capture(metadata, /^\s+- url:\s*(.+)$/m, 'files[0].url');
const fileSha = capture(metadata, /^\s{4}sha512:\s*(.+)$/m, 'files[0].sha512');
const declaredSize = Number(capture(metadata, /^\s{4}size:\s*(.+)$/m, 'files[0].size'));
const legacyPath = capture(metadata, /^path:\s*(.+)$/m, 'path');
const legacySha = capture(metadata, /^sha512:\s*(.+)$/m, 'sha512');
const releaseDate = capture(metadata, /^releaseDate:\s*(.+)$/m, 'releaseDate');

assert.equal(version, expectedVersion, 'Update version follows frontend/package.json');
assert.equal(fileUrl, basename(fileUrl), 'Update artifact URL stays release-relative');
assert.equal(legacyPath, fileUrl, 'Legacy path agrees with files[0].url');
assert.equal(legacySha, fileSha, 'Legacy SHA-512 agrees with files[0].sha512');
assert(Number.isSafeInteger(declaredSize) && declaredSize > 0, 'Update size is a positive integer');
assert(!Number.isNaN(Date.parse(releaseDate)), 'Update releaseDate is an ISO timestamp');

const artifactPath = resolve(releaseDir, fileUrl);
assert.equal(
  dirname(artifactPath),
  releaseDir,
  'Update artifact cannot escape the release directory',
);
assert(existsSync(artifactPath), `Update artifact exists: ${fileUrl}`);
assert.equal(
  statSync(artifactPath).size,
  declaredSize,
  'Update metadata size matches artifact bytes',
);
const hashes = await fileHashes(artifactPath, platform === 'linux');
assert.equal(hashes.sha512, fileSha, 'Update metadata SHA-512 matches artifact bytes');

const osToken = platform === 'win32' ? 'win' : platform === 'darwin' ? 'mac' : 'linux';
assert(
  fileUrl.startsWith(`VoiceStudio-Electron-${expectedVersion}-${osToken}-${arch}.`),
  'Update artifact preserves the branded version/platform/architecture name',
);

if (platform === 'win32') {
  assert(fileUrl.endsWith('.exe'), 'Windows update artifact is the NSIS installer');
  assert(existsSync(`${artifactPath}.blockmap`), 'Windows differential update blockmap exists');
} else if (platform === 'darwin') {
  assert(fileUrl.endsWith('.zip'), 'macOS update artifact is the updater ZIP');
} else {
  assert(fileUrl.endsWith('.AppImage'), 'Linux update artifact is the AppImage');
  const runtimeSections = execFileSync('readelf', ['-SW', artifactPath], {
    encoding: 'utf8',
  });
  assert.match(
    runtimeSections,
    /^\s*\[\s*\d+\]\s+\.static\s+PROGBITS/m,
    'AppImage uses the FUSE2-independent static runtime',
  );
  const preview = channel === 'electron-preview-linux-x64';
  const updateInfo = execFileSync(artifactPath, ['--appimage-updateinformation'], {
    encoding: 'utf8',
  }).trim();
  assert.equal(
    updateInfo,
    `gh-releases-zsync|debpalash|VoiceStudio|${preview ? 'preview' : 'latest'}|VoiceStudio-Electron-*-linux-x64.AppImage.zsync`,
    'External AppImage updaters follow the selected Linux release channel',
  );
  const control = `${artifactPath}.zsync`;
  assert(existsSync(control), 'Versioned AppImage zsync control file exists');
  const header = readFileSync(control).subarray(0, 4096).toString('utf8');
  assert(
    header.includes(
      `URL: https://github.com/debpalash/VoiceStudio/releases/download/${preview ? 'preview' : `v${expectedVersion}`}/${fileUrl}\n`,
    ),
    'zsync downloads the matching release-channel artifact',
  );
  assert(header.includes(`Length: ${declaredSize}\n`), 'zsync describes final AppImage size');
  assert(header.includes(`SHA-1: ${hashes.sha1}\n`), 'zsync verifies the final AppImage bytes');
}

console.log(
  `PASS: ${basename(metadataPath)} -> ${fileUrl} (${declaredSize} bytes, SHA-512 verified)`,
);
