import { describe, expect, it } from 'vitest';

// #3924: what the renderer entry reaches through static imports is what
// index.html loads at startup. The media players (Vidstack and WaveSurfer,
// about 500 KB) belong behind a dynamic import, loaded with the first clip.
const LAZY_ONLY = ['@vidstack/react', 'wavesurfer.js'];

const rendererSources = import.meta.glob('./**/*.{ts,tsx}', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>;
const sharedSources = import.meta.glob('../../shared/**/*.{ts,tsx}', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>;

// Module ids: renderer files relative to src/renderer/src, shared ones as `@shared/...`.
const sources = new Map<string, string>([
  ...Object.entries(rendererSources).map(([key, src]) => [key.slice(2), src] as const),
  ...Object.entries(sharedSources).map(
    ([key, src]) => ['@shared/' + key.slice('../../shared/'.length), src] as const,
  ),
]);

// Every static module edge: `import x from`, `import {a} from` (also across
// lines), `import * as ns from`, `export * from`, `export * as ns from`,
// `export {x} from` and side-effect `import 'x'`. Type-only edges and
// `import(...)` are not static runtime edges.
const STATIC_IMPORT =
  /\b(?:import|export)\b(?!\s*\()\s*(type\b\s*(?=[{*\w]))?(?:(?:(?!\b(?:import|export)\b)[^'";])*?\bfrom\s*)?['"]([^'"\n]+)['"]/g;

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');
}

/** Runtime (non-type) specifiers imported or re-exported by `src`. */
export function staticSpecifiers(src: string): string[] {
  const out: string[] = [];
  for (const [, typeOnly, spec] of stripComments(src).matchAll(STATIC_IMPORT))
    if (!typeOnly) out.push(spec);
  return out;
}

function join(dir: string, spec: string): string {
  const parts = dir ? dir.split('/') : [];
  for (const part of spec.split('/')) {
    if (part === '..') parts.pop();
    else if (part !== '.') parts.push(part);
  }
  return parts.join('/');
}

function resolve(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith('@/')) base = spec.slice(2);
  else if (spec.startsWith('@shared/')) base = spec;
  else if (spec.startsWith('.')) base = join(from.split('/').slice(0, -1).join('/'), spec);
  else return null;
  for (const candidate of [
    base,
    base + '.ts',
    base + '.tsx',
    base + '/index.ts',
    base + '/index.tsx',
  ])
    if (sources.has(candidate)) return candidate;
  return null;
}

/** Every bare package reachable from `entry`, with the chain of files that reaches it. */
function staticPackages(entry: string): Map<string, string[]> {
  const parent = new Map<string, string | null>([[entry, null]]);
  const packages = new Map<string, string[]>();
  const queue = [entry];
  const chain = (file: string) => {
    const out: string[] = [];
    for (let at: string | null = file; at; at = parent.get(at) ?? null) out.unshift(at);
    return out;
  };
  while (queue.length) {
    const file = queue.shift()!;
    for (const spec of staticSpecifiers(sources.get(file)!)) {
      const target = resolve(file, spec);
      if (target) {
        if (!parent.has(target)) {
          parent.set(target, file);
          queue.push(target);
        }
      } else if (!spec.startsWith('.') && !spec.startsWith('@/') && !packages.has(spec)) {
        packages.set(spec, chain(file));
      }
    }
  }
  return packages;
}

describe('staticSpecifiers', () => {
  it.each([
    ["import x from 'a';", ['a']],
    ["import { x, y as z } from 'a';", ['a']],
    ["import x, { y } from 'a';", ['a']],
    ["import * as ns from 'a';", ['a']],
    ["import 'a';", ['a']],
    ["import {\n  x,\n  y,\n} from 'a';", ['a']],
    ["export * from 'a';", ['a']],
    ["export * as ns from 'a';", ['a']],
    ["export { x } from 'a';", ['a']],
    ["export {\n  x,\n  y\n} from 'a'", ['a']],
    ["export { default } from 'a'", ['a']],
    ["const a = 1\nimport x from 'a'\nexport { y } from 'b'", ['a', 'b']],
    ["import type { X } from 'a';", []],
    ["export type { X } from 'a';", []],
    ["import('a').then((m) => m.x);", []],
    ["export const x = 1;\nconst y = await import('a');", []],
    ["// import x from 'a'\n/* export * from 'b' */", []],
  ] as [string, string[]][])('%j', (src, expected) => {
    expect(staticSpecifiers(src)).toEqual(expected);
  });
});

describe('renderer startup imports', () => {
  it('finds the entry and its static imports', () => {
    const packages = staticPackages('main.tsx');
    expect(packages.has('react')).toBe(true);
  });

  it('keeps the media players out of the startup bundle', () => {
    const packages = staticPackages('main.tsx');
    const eager = [...packages]
      .filter(([spec]) => LAZY_ONLY.some((name) => spec === name || spec.startsWith(name + '/')))
      .map(([spec, chain]) => spec + ' via ' + chain.join(' -> '));
    expect(eager).toEqual([]);
  });
});
