import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

describe('responsive dub timeline sizing', () => {
  it('releases both forced track dimensions inside the narrow workspace', () => {
    const targetStart = css.lastIndexOf('@container dub-shell (max-width: 960px)');
    const targetEnd = css.indexOf('@container dub-shell', targetStart + 1);
    const targetContainer = css.slice(targetStart, targetEnd);
    const narrow = targetContainer.match(/\.dub-panel-left \.seg-track \{([^}]*)\}/);

    expect(targetStart).toBeGreaterThanOrEqual(0);
    expect(targetEnd).toBeGreaterThan(targetStart);
    expect(narrow?.[1]).toMatch(/flex:\s*0 0 auto !important/);
    expect(narrow?.[1]).toMatch(/height:\s*auto/);
  });
});
