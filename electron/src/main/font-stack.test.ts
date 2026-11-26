import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// #4478: pages that cannot load the bundled webfont (data: URLs, main-process
// HTML) must not name locally installed fonts such as "Inter", which a user may
// have installed as a broken or symbol-encoded face.
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return walk(p);
    return /\.(ts|html)$/.test(n) && !/\.test\./.test(n) ? [p] : [];
  });
}

const ALLOWED =
  /^(-apple-system|BlinkMacSystemFont|'Segoe UI'|"Segoe UI"|system-ui|sans-serif|monospace|ui-monospace|inherit)$/;

describe('main-process font stacks', () => {
  it('never reference a non-bundled named font', () => {
    let checked = 0;
    for (const file of walk(__dirname)) {
      const src = readFileSync(file, 'utf8');
      for (const m of src.matchAll(
        /font(?:-family)?\s*:\s*(?:[\d.]+\w*(?:\/[\d.]+\w*)?\s+)?([^;}`]+)/g,
      )) {
        const families = m[1].split(',').map((f) => f.trim());
        if (families.length < 2) continue;
        checked += 1;
        for (const f of families) expect(f, `${file}: ${m[0]}`).toMatch(ALLOWED);
      }
    }
    expect(checked).toBeGreaterThan(0);
  });
});
