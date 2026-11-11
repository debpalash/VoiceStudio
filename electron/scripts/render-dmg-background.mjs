// Renders the macOS DMG window background from its SVG source.
//
//   bun run dmg:background          (from electron/)
//
// Writes build/dmg-background.png (1x) and build/dmg-background@2x.png (2x).
// electron-builder merges the pair into a HiDPI TIFF and sizes the Finder
// window from the 1x image, so both must keep exactly the SVG's size and its
// double (tests/dmg-window.test.mjs). Needs rsvg-convert (librsvg) and sips;
// run it on macOS so the headline uses the system font Finder shows.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(root, 'build/dmg/background.svg');
const svg = readFileSync(source, 'utf8');
const width = Number(svg.match(/<svg[^>]*\swidth="(\d+)"/)?.[1]);
const height = Number(svg.match(/<svg[^>]*\sheight="(\d+)"/)?.[1]);
if (!width || !height) throw new Error(`${source}: missing integer width/height`);

function run(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  if (result.error) throw new Error(`${command} is required: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${command} failed: ${result.stderr}`);
}

for (const [scale, name] of [
  [1, 'dmg-background.png'],
  [2, 'dmg-background@2x.png'],
]) {
  const output = resolve(root, 'build', name);
  run('rsvg-convert', ['-w', `${width * scale}`, '-h', `${height * scale}`, source, '-o', output]);
  // Finder reads the DPI to pair the images: 72 for 1x, 144 for 2x.
  const dpi = `${72 * scale}`;
  run('sips', ['-s', 'dpiWidth', dpi, '-s', 'dpiHeight', dpi, output]);
  console.log(`${name}: ${width * scale}x${height * scale}`);
}
