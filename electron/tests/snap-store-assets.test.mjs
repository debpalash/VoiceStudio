import assert from 'node:assert/strict';
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';

import { createSnapStoreAssets } from '../../scripts/create_snap_store_assets.mjs';

test('derives a bounded Snap Store media set from canonical listing assets', () => {
  const outputDir = mkdtempSync(join(tmpdir(), 'voicestudio-snap-assets-'));
  const manifest = createSnapStoreAssets({ outputDir });

  assert.equal(manifest.canonicalSource, 'store/assets.json');
  assert.equal(manifest.icon.width, 512);
  assert.equal(manifest.icon.height, 512);
  assert.equal(manifest.screenshots.length, 5);
  assert.equal(manifest.banner.file, 'banner.png');
  assert.equal(manifest.banner.width, 3 * manifest.banner.height);
  assert(manifest.banner.width >= 720 && manifest.banner.bytes <= 2 * 1024 * 1024);
  assert(existsSync(join(outputDir, manifest.banner.file)));
  assert(existsSync(join(outputDir, manifest.icon.file)));
  for (const screenshot of manifest.screenshots) {
    assert(screenshot.width >= 480 && screenshot.height >= 480);
    assert(screenshot.width / screenshot.height >= 0.5);
    assert(screenshot.width / screenshot.height <= 2);
    assert(screenshot.bytes <= 2 * 1024 * 1024);
    assert.match(screenshot.sha256, /^[a-f0-9]{64}$/);
    assert(existsSync(join(outputDir, screenshot.file)));
  }
  assert.deepEqual(
    JSON.parse(readFileSync(join(outputDir, 'listing-assets.json'), 'utf8')),
    manifest,
  );
});

/** A PNG signature and IHDR header: all the banner validator reads. */
function pngHeader(width, height) {
  const header = Buffer.alloc(24);
  Buffer.from('89504e470d0a1a0a', 'hex').copy(header);
  header.writeUInt32BE(13, 8);
  header.write('IHDR', 12, 'ascii');
  header.writeUInt32BE(width, 16);
  header.writeUInt32BE(height, 20);
  return header;
}

for (const [label, size] of [
  ['square', null],
  ['almost 3:1 (722x240)', [722, 240]],
]) {
  test(`rejects a banner that is not exactly 3:1: ${label}`, () => {
    const dir = mkdtempSync(join(tmpdir(), 'voicestudio-snap-banner-'));
    const assets = JSON.parse(
      readFileSync(new URL('../../store/assets.json', import.meta.url), 'utf8'),
    );
    mkdirSync(join(dir, 'store'));
    let snapBannerSource = assets.iconSource;
    if (size) {
      snapBannerSource = 'store/banner-test.png';
      writeFileSync(join(dir, snapBannerSource), pngHeader(...size));
    }
    writeFileSync(join(dir, 'store/assets.json'), JSON.stringify({ ...assets, snapBannerSource }));
    for (const path of [assets.iconSource, ...assets.screenshots.map((item) => item.source)]) {
      mkdirSync(dirname(join(dir, path)), { recursive: true });
      copyFileSync(new URL(`../../${path}`, import.meta.url), join(dir, path));
    }
    assert.throws(
      () =>
        createSnapStoreAssets({
          assetsPath: join(dir, 'store/assets.json'),
          outputDir: join(dir, 'out'),
        }),
      /banner must be 3:1/,
    );
  });
}
