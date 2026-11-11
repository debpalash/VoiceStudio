// Put SignPath-signed Windows installers back into electron/release and bring
// the updater feed back in line with them.
//
// electron-builder writes electron-<channel>-win32-<arch>.yml (sha512, size) and
// <installer>.exe.blockmap at package time, from the UNSIGNED installer. Signing
// appends an Authenticode signature, so both the bytes and the hash change.
// Publishing the stale feed would make every updater reject the download
// ("sha512 checksum mismatch"), and a stale blockmap corrupts differential
// updates. So, after signing and before upload:
//   1. replace each installer with its signed counterpart (same file name),
//   2. regenerate the blockmap with electron-builder's own app-builder binary,
//      then verify it describes exactly the signed bytes,
//   3. rewrite sha512/size in the feed (files[] and the legacy top-level key).
// The release job re-checks sha512 against the bytes (prepare_electron_release.py)
// and SHA256SUMS.txt is computed there, after this step.
//
// Usage: node scripts/apply-signed-windows.mjs <signedDir> [releaseDir]
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync, inflateRawSync } from 'node:zlib';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const signedDir = resolve(process.argv[2] ?? '');
const releaseDir = resolve(root, process.argv[3] ?? 'release');
assert(process.argv[2], 'Usage: apply-signed-windows.mjs <signedDir> [releaseDir]');

const sha512Base64 = (path) => createHash('sha512').update(readFileSync(path)).digest('base64');

function blockmapPath(exe) {
  return `${exe}.blockmap`;
}

function regenerateBlockmap(exe) {
  const require = createRequire(import.meta.url);
  const builderLib = dirname(require.resolve('app-builder-lib/package.json'));
  const { appBuilderPath } = createRequire(`${builderLib}/`)('app-builder-bin');
  execFileSync(appBuilderPath, ['blockmap', '--input', exe, '--output', blockmapPath(exe)], {
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  // Independent proof: the blockmap's chunk sizes must add up to the signed file.
  // electron-updater reads it as raw deflate; accept gzip too in case that changes.
  const packed = readFileSync(blockmapPath(exe));
  let unpacked;
  try {
    unpacked = inflateRawSync(packed);
  } catch {
    unpacked = gunzipSync(packed);
  }
  const map = JSON.parse(unpacked.toString('utf8'));
  const described = map.files.reduce((sum, f) => sum + f.sizes.reduce((a, b) => a + b, 0), 0);
  assert.equal(described, statSync(exe).size, `Blockmap describes the signed bytes of ${exe}`);
}

const signedExes = readdirSync(signedDir).filter((name) => /^VoiceStudio-.*\.exe$/.test(name));
const releaseExes = readdirSync(releaseDir).filter((name) => /^VoiceStudio-.*\.exe$/.test(name));
assert(releaseExes.length > 0, 'No installer to replace');
assert.deepEqual(
  signedExes.sort(),
  releaseExes.sort(),
  'SignPath returned exactly the installers that were submitted',
);

const feeds = readdirSync(releaseDir).filter((name) => /^electron-.*win32-.*\.yml$/.test(name));
assert(feeds.length > 0, 'No Windows update feed to patch');

for (const name of releaseExes) {
  const exe = resolve(releaseDir, name);
  const before = statSync(exe).size;
  copyFileSync(resolve(signedDir, name), exe);
  assert(statSync(exe).size > before, `Signed ${name} should carry a signature (grow in size)`);
  regenerateBlockmap(exe);
  const sha512 = sha512Base64(exe);
  const size = statSync(exe).size;
  let patched = 0;
  for (const feed of feeds) {
    const path = resolve(releaseDir, feed);
    let text = readFileSync(path, 'utf8');
    if (!text.includes(name)) continue;
    // files[0] entry: `- url: <name>` then 4-space `sha512:` and `size:`.
    text = text.replace(
      /^(\s+- url:\s*['"]?VoiceStudio-[^\n]*\n)(\s{4}sha512:\s*)[^\n]*\n(\s{4}size:\s*)[^\n]*\n/m,
      (_m, url, shaKey, sizeKey) => `${url}${shaKey}${sha512}\n${sizeKey}${size}\n`,
    );
    text = text.replace(/^sha512:\s*[^\n]*$/m, `sha512: ${sha512}`);
    assert(text.includes(sha512), `Feed ${feed} now carries the signed hash`);
    assert(!/blockMapSize/.test(text), 'NSIS feeds carry no blockMapSize; revisit this script');
    writeFileSync(path, text);
    patched += 1;
  }
  assert(patched > 0, `A feed references ${name}`);
  console.log(`signed ${name}: ${size} bytes, sha512 ${sha512.slice(0, 12)}...`);
}
