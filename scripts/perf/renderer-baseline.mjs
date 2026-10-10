#!/usr/bin/env node
// Renderer performance baseline: the files index.html references (fetched at
// page load), and how much background polling the UI declares. Builds the web renderer into a temp
// directory (no repo output is touched) and prints a markdown summary.
//
//   node scripts/perf/renderer-baseline.mjs
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

const repo = resolve(import.meta.dirname, '../..');
const electron = join(repo, 'electron');
const out = mkdtempSync(join(tmpdir(), 'vs-perf-'));

execFileSync(
  'bunx',
  ['vite', 'build', '--config', 'vite.web.config.ts', '--outDir', out, '--emptyOutDir'],
  { cwd: electron, stdio: 'ignore' },
);

const kb = (n) => `${(n / 1024).toFixed(0)} KB`;
const assets = readdirSync(join(out, 'assets')).map((name) => ({
  name,
  size: statSync(join(out, 'assets', name)).size,
}));
const js = assets.filter((a) => a.name.endsWith('.js'));

// Everything index.html pulls in before the app can render.
const html = readFileSync(join(out, 'index.html'), 'utf8');
const eager = [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]);
const eagerRows = eager.map((file) => {
  const bytes = readFileSync(join(out, file));
  return { file, raw: bytes.length, gzip: gzipSync(bytes).length };
});
const sum = (rows, key) => rows.reduce((total, row) => total + row[key], 0);

const LOCALE = /^(ar|de|es|fr|hi|id|it|ja|ko|nl|pl|pt|ru|sv|th|tr|uk|vi|zh-CN|zh-TW)-/;
const locales = js.filter((a) => LOCALE.test(a.name));

console.log('### Renderer bundle');
console.log(`- Referenced by index.html: ${eager.length} files, ${kb(sum(eagerRows, 'raw'))} raw, ${kb(sum(eagerRows, 'gzip'))} gzip`);
console.log(`- JS chunks: ${js.length}; locale chunks: ${locales.length}, ${kb(locales.reduce((t, a) => t + a.size, 0))} (lazy)`);
console.log('\nLargest files referenced by index.html:');
for (const row of eagerRows.sort((a, b) => b.raw - a.raw).slice(0, 8)) {
  console.log(`- ${row.file.replace('assets/', '')}: ${kb(row.raw)} raw, ${kb(row.gzip)} gzip`);
}
const lazy = js.filter((a) => !eager.includes(`assets/${a.name}`) && !LOCALE.test(a.name));
console.log('\nLargest lazy chunks:');
for (const a of lazy.sort((x, y) => y.size - x.size).slice(0, 6)) console.log(`- ${a.name}: ${kb(a.size)}`);

// Declared polling: query intervals and bare timers in renderer source.
const files = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\./.test(name)) files.push(path);
  }
};
walk(join(electron, 'src/renderer/src'));
let intervals = 0;
let timers = 0;
const withIntervals = [];
for (const file of files) {
  const text = readFileSync(file, 'utf8');
  const count = (text.match(/refetchInterval(?!InBackground)/g) ?? []).length;
  intervals += count;
  timers += (text.match(/setInterval\(/g) ?? []).length;
  if (count) withIntervals.push(relative(electron, file));
}
console.log('\n### Polling (declared in source)');
console.log(`- refetchInterval: ${intervals} across ${withIntervals.length} files; setInterval: ${timers}`);
