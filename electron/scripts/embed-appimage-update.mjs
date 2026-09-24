// Add AppImageUpdate metadata without repacking the AppImage. Repacking loses
// electron-updater's embedded blockmap and changes the bundled launcher.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { open, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { basename, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateRawSync } from 'node:zlib';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const releaseDir = resolve(root, process.argv[2] || 'release');
const version = JSON.parse(readFileSync(resolve(root, '../frontend/package.json'), 'utf8')).version;
const filename = `VoiceStudio-Electron-${version}-linux-x64.AppImage`;
const image = resolve(releaseDir, filename);
const zsync = `${image}.zsync`;
const channel = process.env.VOICESTUDIO_UPDATE_CHANNEL || 'electron-stable-linux-x64';
assert(
  ['electron-stable-linux-x64', 'electron-preview-linux-x64'].includes(channel),
  'Known Linux updater channel',
);
const preview = channel === 'electron-preview-linux-x64';
const releaseTag = preview ? 'preview' : `v${version}`;
const manifestPath = resolve(releaseDir, `${channel}-linux.yml`);
const updateInfo =
  `gh-releases-zsync|debpalash|VoiceStudio|${preview ? 'preview' : 'latest'}|` +
  'VoiceStudio-Electron-*-linux-x64.AppImage.zsync';

// The pinned electron-builder runtime reserves a fixed-size .upd_info ELF
// section. Read its actual offset instead of assuming a particular runtime
// build, and fail rather than shifting the following squashfs payload.
const sections = execFileSync('readelf', ['-SW', image], { encoding: 'utf8' });
const section = sections.match(
  /^\s*\[\s*\d+\]\s+\.upd_info\s+PROGBITS\s+[0-9a-f]+\s+([0-9a-f]+)\s+([0-9a-f]+)\b/im,
);
assert(section, 'AppImage runtime has a reserved .upd_info ELF section');
const offset = parseInt(section[1], 16);
const capacity = parseInt(section[2], 16);
const encoded = Buffer.from(`${updateInfo}\0`);
assert(
  capacity >= encoded.length && capacity <= 4096 && offset > 0,
  'AppImage update section fits within its reserved runtime bytes',
);

// electron-updater appends a deflate blockmap followed by its 4-byte size.
// The new ELF bytes must be included in a freshly generated blockmap; never
// publish an artifact whose own differential map describes the old contents.
const file = await open(image, 'r+');
try {
  const { size } = await file.stat();
  assert(offset + capacity < size - 4, 'Update section is inside the AppImage runtime');
  const sizeHeader = Buffer.alloc(4);
  assert.equal((await file.read(sizeHeader, 0, 4, size - 4)).bytesRead, 4);
  const oldMapSize = sizeHeader.readUInt32BE(0);
  assert(
    oldMapSize > 0 && oldMapSize < size - offset - capacity - 4,
    'AppImage has an embedded blockmap',
  );
  const baseSize = size - oldMapSize - 4;
  const oldMap = Buffer.alloc(oldMapSize);
  assert.equal((await file.read(oldMap, 0, oldMapSize, baseSize)).bytesRead, oldMapSize);
  const chunks = JSON.parse(inflateRawSync(oldMap).toString('utf8'));
  assert.equal(
    chunks.files[0].sizes.reduce((sum, n) => sum + n, 0),
    baseSize,
    'Existing blockmap describes the AppImage bytes',
  );
  const reserved = Buffer.alloc(capacity);
  assert.equal((await file.read(reserved, 0, capacity, offset)).bytesRead, capacity);
  assert(
    reserved.every((byte) => byte === 0),
    'AppImage update section is empty before embedding',
  );
  assert.equal((await file.write(encoded, 0, encoded.length, offset)).bytesWritten, encoded.length);
  await file.truncate(baseSize);
} finally {
  await file.close();
}

// Use the blockmap generator from the exact electron-builder version that
// created the AppImage. Its chunking and trailer are part of electron-updater's
// wire format; a second home-grown implementation could silently diverge.
const require = createRequire(import.meta.url);
const builderRoot = dirname(require.resolve('electron-builder/package.json'));
const { appendBlockmap } = require(
  require.resolve('app-builder-lib/out/targets/differentialUpdateInfoBuilder.js', {
    paths: [builderRoot],
  }),
);
const { size, sha512, blockMapSize } = await appendBlockmap(image);
assert.equal((await stat(image)).size, size);

const url = `https://github.com/debpalash/VoiceStudio/releases/download/${releaseTag}/${filename}`;
execFileSync('zsyncmake', ['-u', url, '-o', zsync, image], { stdio: 'inherit' });

const manifest = readFileSync(manifestPath, 'utf8');
const old = manifest.match(
  /^  - url: ([^\n]+)\n    sha512: ([^\n]+)\n    size: (\d+)\n    blockMapSize: (\d+)/m,
);
assert(old && old[1] === filename, 'Linux updater manifest points at this AppImage');
const legacy = `path: ${filename}\nsha512: ${old[2]}`;
assert(manifest.includes(legacy), 'Legacy updater hash agrees with AppImage entry');
const updated = manifest
  .replace(
    old[0],
    `  - url: ${filename}\n    sha512: ${sha512}\n    size: ${size}\n    blockMapSize: ${blockMapSize}`,
  )
  .replace(legacy, `path: ${filename}\nsha512: ${sha512}`);
writeFileSync(manifestPath, updated);
console.log(`Embedded AppImageUpdate metadata and generated ${basename(zsync)}`);
