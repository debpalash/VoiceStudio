import { describe, it, expect } from 'vitest';
import { fmtBytes, freedBytes } from '../components/settings/UninstallPanel.jsx';

// #1089: the number on the confirm button must equal what actually gets deleted.
// The shared Hugging Face cache is OPT-IN — it's the standard HF cache other ML
// tools share, so it must never be counted (or removed) unless explicitly ticked.

const TARGETS = [
  { key: 'data', size_bytes: 100, exists: true, shared: false },
  { key: 'env', size_bytes: 1000, exists: true, shared: false },
  { key: 'logs', size_bytes: 10, exists: true, shared: false },
  { key: 'models', size_bytes: 50_000, exists: true, shared: true },
];

describe('freedBytes — the shared model cache is opt-in', () => {
  it('excludes the shared cache by default', () => {
    expect(freedBytes(TARGETS, false)).toBe(1110);
  });

  it('includes the shared cache only when opted in', () => {
    expect(freedBytes(TARGETS, true)).toBe(51_110);
  });

  it('ignores folders that do not exist', () => {
    const some = [
      { key: 'data', size_bytes: 100, exists: false, shared: false },
      { key: 'env', size_bytes: 7, exists: true, shared: false },
    ];
    expect(freedBytes(some, true)).toBe(7);
  });

  it('is safe on empty/undefined input', () => {
    expect(freedBytes([], false)).toBe(0);
    expect(freedBytes(undefined, true)).toBe(0);
  });
});

describe('fmtBytes', () => {
  it('scales units and keeps sizes readable', () => {
    expect(fmtBytes(0)).toBe('0 B');
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(1024)).toBe('1.0 KB');
    expect(fmtBytes(1536)).toBe('1.5 KB');
    expect(fmtBytes(5 * 1024 ** 3)).toBe('5.0 GB');
    expect(fmtBytes(20 * 1024 ** 3)).toBe('20 GB');
  });

  it('never renders a negative or bogus size', () => {
    expect(fmtBytes(-5)).toBe('0 B');
    expect(fmtBytes(NaN)).toBe('0 B');
    expect(fmtBytes(undefined)).toBe('0 B');
  });
});
