// The macOS DMG install window: background, window size, icon slots, title.
// Finder geometry below was measured with the accessibility tree on macOS 26:
// the window frame equals the background, a 112 pt icon's cell ends 113 pt
// below its centre, and the title, tab, path and status bars take 124 pt.
// An icon cell past the visible area makes Finder add a scroll bar, which
// showed as a white strip down the window's right edge.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import config from '../electron-builder.config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dmg = config.dmg;
const ICON_CELL_BELOW_CENTRE = 113;
const FINDER_BARS_WITH_EVERYTHING_SHOWN = 124;

function pngSize(path) {
  const bytes = readFileSync(resolve(root, path));
  assert.equal(bytes.toString('latin1', 1, 4), 'PNG', `${path} is a PNG`);
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

function svgSize() {
  const svg = readFileSync(resolve(root, 'build/dmg/background.svg'), 'utf8');
  const tag = svg.match(/<svg\b[^>]*>/)[0];
  return {
    width: Number(tag.match(/\swidth="(\d+)"/)[1]),
    height: Number(tag.match(/\sheight="(\d+)"/)[1]),
  };
}

test('the background renders from its SVG at 1x and exactly 2x', () => {
  assert.equal(dmg.background, 'build/dmg-background.png');
  const source = svgSize();
  const one = pngSize('build/dmg-background.png');
  const two = pngSize('build/dmg-background@2x.png');
  assert.deepEqual(one, source, 're-render with `bun run dmg:background`');
  assert.deepEqual(two, { width: one.width * 2, height: one.height * 2 });
});

test('the window matches the background, so no strip of Finder white shows', () => {
  assert.deepEqual(dmg.window, pngSize('build/dmg-background.png'));
});

test('the app and Applications sit inside the window without a scroll bar', () => {
  const { width, height } = dmg.window;
  const half = dmg.iconSize / 2;
  assert.equal(dmg.contents.length, 2);
  assert.ok(dmg.contents.some((item) => item.type === 'file' && item.path === undefined));
  assert.ok(dmg.contents.some((item) => item.type === 'link' && item.path === '/Applications'));
  for (const { x, y } of dmg.contents) {
    assert.ok(x - half >= 0 && x + half <= width, `icon at x=${x} fits the width`);
    assert.ok(y - half >= 0, `icon at y=${y} fits below the top`);
    assert.ok(
      y + ICON_CELL_BELOW_CENTRE <= height - FINDER_BARS_WITH_EVERYTHING_SHOWN,
      `icon at y=${y} stays visible with every Finder bar shown`,
    );
  }
  const svg = readFileSync(resolve(root, 'build/dmg/background.svg'), 'utf8');
  for (const { x, y } of dmg.contents) {
    assert.match(svg, new RegExp(`\\(${x}, ${y}\\)`), 'the SVG notes the same icon slots');
  }
});

test('the volume title has no architecture and the file name is unchanged', () => {
  assert.equal(dmg.title, '${productName} ${version}');
  assert.doesNotMatch(dmg.title, /\$\{arch\}/);
  assert.equal(dmg.artifactName, undefined, 'DMGs keep VoiceStudio-<version>-mac-<arch>.dmg');
  assert.equal(config.mac.artifactName, undefined);
  assert.match(config.artifactName, /-\$\{version\}-\$\{os\}-\$\{arch\}\.\$\{ext\}$/);
});
