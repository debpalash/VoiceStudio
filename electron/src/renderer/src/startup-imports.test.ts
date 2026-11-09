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

// `import x from`, `import {a} from`, `export * from`, `import 'side-effect'`;
// not `import type`/`export type` and not `import(...)`.
const STATIC_IMPORT =
  /(?:^|[;\n])\s*(?:import|export)\s+(type\s+)?(?:[\w$*{}\s,]+?\s+from\s+)?['"]([^'"]+)['"]/g;

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
    for (const [, typeOnly, spec] of sources.get(file)!.matchAll(STATIC_IMPORT)) {
      if (typeOnly) continue;
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
