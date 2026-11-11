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
// Usage (cwd = electron/): node apply-signed-windows.mjs <signedDir> [releaseDir]
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { gunzipSync, inflateRawSync } from 'node:zlib';

const signedDir = resolve(process.argv[2] ?? '');
const releaseDir = resolve(process.argv[3] ?? 'release');
assert(process.argv[2], 'Usage: apply-signed-windows.mjs <signedDir> [releaseDir]');

const sha512Base64 = (path) => createHash('sha512').update(readFileSync(path)).digest('base64');

function blockmapPath(exe) {
  return `${exe}.blockmap`;
}

// electron-builder's own blockmap writer (pure JS since 26.x), so the map is
// byte-for-byte what a normal build would have produced for these bytes.
// Resolved hop by hop (electron -> electron-builder -> app-builder-lib) so bun's
// isolated layout works; cwd must be electron/ (the workflow loads this script
// from its own commit, so cwd, not the script location, anchors resolution).
function resolveBuildBlockMap() {
  try {
    const fromElectron = createRequire(resolve(process.cwd(), 'package.json'));
    const fromBuilder = createRequire(fromElectron.resolve('electron-builder'));
    const fromLib = createRequire(fromBuilder.resolve('app-builder-lib'));
    const { buildBlockMap } = fromLib('app-builder-lib/out/targets/blockmap/blockmap');
    assert.equal(typeof buildBlockMap, 'function', 'buildBlockMap export');
    return buildBlockMap;
  } catch (error) {
    throw new Error(
      `Cannot load electron-builder's blockmap writer from ${process.cwd()} (${error.message}); run from electron/ after bun install, and re-check this script after an electron-builder upgrade`,
    );
  }
}

async function regenerateBlockmap(exe) {
  const info = await resolveBuildBlockMap()(exe, 'gzip', blockmapPath(exe));
  assert.equal(info.sha512, sha512Base64(exe), 'Blockmap writer hashed the signed bytes');
  // Independent proof: the blockmap's chunk sizes must add up to the signed file.
  // The standalone installer blockmap is gzip (the embedded AppImage one is raw deflate).
  const packed = readFileSync(blockmapPath(exe));
  let unpacked;
  try {
    unpacked = gunzipSync(packed);
  } catch {
    unpacked = inflateRawSync(packed);
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
  await regenerateBlockmap(exe);
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
