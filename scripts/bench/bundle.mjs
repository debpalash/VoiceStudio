#!/usr/bin/env node
// Renderer bundle size: per-chunk raw and gzip sizes of a built renderer, the
// files index.html loads at startup, and the ten largest chunks. Reads an
// existing build (default: electron/out/renderer, written by
// `bun run --cwd electron build`); it never builds anything itself.
//
//   node scripts/bench/bundle.mjs                       # markdown summary
//   node scripts/bench/bundle.mjs --json                # JSON on stdout
//   node scripts/bench/bundle.mjs --check               # exit 1 on a >10% regression
//   node scripts/bench/bundle.mjs --write-baseline      # refresh the recorded budget
//
// --check compares against scripts/bench/bundle-baseline.json (or --baseline <file>). Gzip sizes are
// compared, not raw, because they are what startup actually reads and they move
// less with minifier noise.
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

const repo = resolve(import.meta.dirname, '../..');
const DEFAULT_BASELINE = join(import.meta.dirname, 'bundle-baseline.json');
/** Growth allowed before --check fails. Generous on purpose: this flags real bloat, not noise. */
export const TOLERANCE = 0.1;
/** Metrics --check guards, as keys of `summary`. */
export const CHECKED = ['eager_gzip', 'js_gzip', 'css_gzip', 'largest_eager_gzip'];

const arg = (name) => process.argv.includes(name);
const option = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index > -1 ? process.argv[index + 1] : fallback;
};

/** `index-B4x_9kQz.js` -> `index.js`, so chunks compare across builds. */
export const stableName = (file) => file.replace(/-[\w-]{8}(\.[a-z0-9]+)$/i, '$1');

function walk(dir, files = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, files);
    else files.push(path);
  }
  return files;
}

export function measure(dir) {
  if (!existsSync(join(dir, 'index.html'))) {
    throw new Error(`${dir} has no index.html; run \`bun run --cwd electron build\` first`);
  }
  const html = readFileSync(join(dir, 'index.html'), 'utf8');
  // Scripts, module preloads and stylesheets index.html references: loaded before first paint.
  const eager = new Set(
    [...html.matchAll(/(?:src|href)="\.?\/?(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]),
  );
  const chunks = walk(join(dir, 'assets'))
    .filter((path) => /\.(js|css)$/.test(path))
    .map((path) => {
      const bytes = readFileSync(path);
      const file = relative(dir, path).split('\\').join('/');
      return {
        file,
        name: stableName(file.replace(/^assets\//, '')),
        type: path.endsWith('.css') ? 'css' : 'js',
        eager: eager.has(file),
        raw: bytes.length,
        gzip: gzipSync(bytes, { level: 9 }).length,
      };
    })
    .sort((a, b) => b.gzip - a.gzip);
  const sum = (rows, key) => rows.reduce((total, row) => total + row[key], 0);
  const js = chunks.filter((c) => c.type === 'js');
  const css = chunks.filter((c) => c.type === 'css');
  const loaded = chunks.filter((c) => c.eager);
  return {
    dir: relative(repo, dir) || dir,
    summary: {
      chunks: chunks.length,
      js_chunks: js.length,
      js_raw: sum(js, 'raw'),
      js_gzip: sum(js, 'gzip'),
      css_raw: sum(css, 'raw'),
      css_gzip: sum(css, 'gzip'),
      eager_files: loaded.length,
      eager_raw: sum(loaded, 'raw'),
      eager_gzip: sum(loaded, 'gzip'),
      largest_eager_gzip: loaded[0]?.gzip ?? 0,
    },
    top10: chunks.slice(0, 10).map(({ name, eager: e, raw, gzip }) => ({ name, eager: e, raw, gzip })),
    eager: loaded.map(({ name, raw, gzip }) => ({ name, raw, gzip })),
  };
}

/** Metrics that grew more than TOLERANCE over the baseline. */
export function regressions(current, baseline, tolerance = TOLERANCE) {
  return CHECKED.filter((key) => baseline[key] > 0)
    .map((key) => ({ key, base: baseline[key], now: current[key], growth: current[key] / baseline[key] - 1 }))
    .filter((row) => row.growth > tolerance);
}

const kb = (n) => `${(n / 1024).toFixed(0)} KB`;

export function markdown(result) {
  const s = result.summary;
  const lines = [
    `| Renderer bundle (\`${result.dir}\`) | raw | gzip |`,
    '|---|---|---|',
    `| Loaded by index.html (${s.eager_files} files) | ${kb(s.eager_raw)} | ${kb(s.eager_gzip)} |`,
    `| All JS (${s.js_chunks} chunks) | ${kb(s.js_raw)} | ${kb(s.js_gzip)} |`,
    `| All CSS | ${kb(s.css_raw)} | ${kb(s.css_gzip)} |`,
    '',
    '| Top 10 chunks (gzip) | at startup | raw | gzip |',
    '|---|---|---|---|',
    ...result.top10.map((c) => `| ${c.name} | ${c.eager ? 'yes' : 'lazy'} | ${kb(c.raw)} | ${kb(c.gzip)} |`),
  ];
  return lines.join('\n');
}

if (resolve(process.argv[1] ?? '') === import.meta.filename) {
  const result = measure(resolve(option('--dir', join(repo, 'electron/out/renderer'))));
  const BASELINE = resolve(option('--baseline', DEFAULT_BASELINE));
  if (arg('--write-baseline')) {
    writeFileSync(BASELINE, `${JSON.stringify(result.summary, null, 2)}\n`);
  }
  if (arg('--json')) console.log(JSON.stringify(result, null, 2));
  else console.log(markdown(result));
  if (arg('--check')) {
    const found = regressions(result.summary, JSON.parse(readFileSync(BASELINE, 'utf8')));
    for (const r of found) {
      console.error(
        `Renderer bundle regression: ${r.key} ${kb(r.base)} -> ${kb(r.now)} (+${(r.growth * 100).toFixed(1)}%, budget +${TOLERANCE * 100}%)`,
      );
    }
    if (found.length) {
      console.error(
        'If the growth is intended, refresh the budget: node scripts/bench/bundle.mjs --write-baseline',
      );
      process.exit(1);
    }
  }
}
