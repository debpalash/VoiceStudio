/**
 * Start-truncated paths must not be reordered by the bidi algorithm.
 *
 * The bug (#1848): the preflight detail line and the storage-path row both use
 * `dir="rtl"` purely to move `text-overflow: ellipsis` to the START of the
 * line, so a long path keeps its tail on screen. But `dir="rtl"` makes the
 * whole line an RTL paragraph, and the Unicode bidi algorithm puts a LEADING
 * run of European numbers or neutrals at the visual RIGHT edge of an RTL
 * paragraph. Every detail beginning with a digit or a "/" therefore rendered
 * with its head moved to the end:
 *
 *   `48.0 GB total`                   → `GB total 48.0`
 *   `/home/me/.cache/huggingface`     → `home/me/.cache/huggingface/`
 *
 * Letter-first details ("Python 3.11.15") were unaffected, which is the
 * signature of bidi reordering rather than a data bug.
 *
 * The fix is to isolate the text in a `<bdi>` (implicitly `dir="auto"`), so the
 * content is ordered by its own direction while the box keeps `direction: rtl`
 * for the ellipsis side.
 *
 * jsdom does no bidi resolution, so this pins the DOM SHAPE the fix depends on.
 * Measured for real in headless Chromium 153, same nesting as the preflight
 * row, x offsets of the first and last word relative to the line box:
 *
 *   text                            variant   head     tail
 *   `48.0 GB total`                 bare      x=49     x=20    ← reversed
 *   `48.0 GB total`                 <bdi>     x=0      x=46
 *   `/home/user/.cache/huggingface` bare      x=175    x=107   ← reversed
 *   `/home/user/.cache/huggingface` <bdi>     x=0      x=112
 *   `Python 3.11.15`                bare      x=0      x=44    ← letter-first,
 *                                                                never broken
 *   an overflowing path             <bdi>     x=-142   x=288   ← head still
 *                                                                clipped off
 *                                                                the start
 *
 * The last row is the one that keeps the fix honest: `<bdi>` restores the
 * order without giving up the start-side ellipsis the `dir="rtl"` is there for.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import fs from 'node:fs';
import path from 'node:path';
import i18n from '../i18n';

vi.mock('../components/WizardLibrary', () => ({ default: () => null }));
vi.mock('../components/MediaEngineCard', () => ({ default: () => null }));
vi.mock('../components/MirrorRescue', () => ({ default: () => null }));
vi.mock('../components/DictationDemo', () => ({ default: () => null }));
vi.mock('../components/HfTokenCard', () => ({ default: () => null }));
vi.mock('../api/external', () => ({ openExternal: vi.fn(() => Promise.resolve()) }));

const DETAIL = '48.0 GB total';
const CACHE = '/home/user/.cache/huggingface';

vi.mock('../api/hooks', () => ({
  useSetupStatus: () => ({
    data: { models_ready: true, missing: [], hf_cache_dir: '/tmp/hf' },
    refetch: vi.fn(),
  }),
  usePreflight: () => ({
    data: {
      ok: true,
      has_warnings: false,
      checks: [
        { id: 'ram', label: 'RAM', status: 'pass', detail: '48.0 GB total' },
        { id: 'hf_cache', label: 'Model cache', status: 'pass', detail: CACHE },
        { id: 'python', label: 'Python', status: 'pass', detail: 'Python 3.11.15' },
      ],
    },
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('../api/client', () => ({
  apiJson: vi.fn(() => Promise.resolve({ available: false, prompted: true })),
  apiFetch: vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
  API: '',
}));

import SetupWizard from '../pages/SetupWizard';

const withI18n = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('preflight details survive the start-truncation hack', () => {
  it('wraps every rtl-truncated detail in a bidi isolate', () => {
    const { container } = render(withI18n(<SetupWizard onReady={() => {}} />));
    const boxes = [...container.querySelectorAll('[dir="rtl"]')];
    expect(boxes.length).toBeGreaterThanOrEqual(3);

    for (const box of boxes) {
      // A bare text child is the bug: the reordering happens to text that the
      // rtl paragraph owns directly.
      expect(
        box.querySelector('bdi'),
        `rtl-truncated element renders its text unisolated: ${box.outerHTML}`,
      ).toBeTruthy();
      expect([...box.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.data.trim())).toBe(
        false,
      );
    }

    const detail = boxes.find((b) => b.textContent === DETAIL);
    expect(detail).toBeTruthy();
    // The isolate carries the whole string — splitting it would reintroduce
    // the reordering between the parts.
    expect(detail.querySelector('bdi').textContent).toBe(DETAIL);
    // The tooltip stays on the box, so hover still shows the untruncated value.
    expect(detail.getAttribute('title')).toBe(DETAIL);
  });
});

describe('the rule holds for every start-truncated element in the tree', () => {
  // Recurrence guard: #1848 shipped at TWO call sites written months apart,
  // so a third one is the expected failure mode. Mechanical rule, mechanical
  // test — same reasoning as tests/test_no_hardcoded_cjk.py.
  const SRC = path.resolve(__dirname, '..');
  const walk = (dir) =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) return e.name === 'test' ? [] : walk(full);
      return /\.(jsx?|tsx?)$/.test(e.name) ? [full] : [];
    });

  it('never lets an rtl element own its text directly', () => {
    const offenders = [];
    for (const file of walk(SRC)) {
      const src = fs.readFileSync(file, 'utf8');
      const open = /<([a-zA-Z][\w.]*)\b[^>]*?\bdir="rtl"[^>]*?>/g;
      let m;
      while ((m = open.exec(src)) !== null) {
        if (m[0].endsWith('/>')) continue; // void/self-closing: no children to isolate
        const close = src.indexOf(`</${m[1]}>`, open.lastIndex);
        const body = close === -1 ? src.slice(open.lastIndex) : src.slice(open.lastIndex, close);
        if (!body.includes('<bdi')) {
          const line = src.slice(0, m.index).split('\n').length;
          offenders.push(`${path.relative(SRC, file)}:${line}`);
        }
      }
    }
    expect(
      offenders,
      `dir="rtl" without a <bdi> isolate reorders digit- and slash-leading text (#1848): ${offenders.join(', ')}`,
    ).toEqual([]);
  });
});
