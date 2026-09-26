/**
 * The header's status dot (Header.jsx) pulses via the `hqPulse` keyframes
 * animation, applied as a Tailwind arbitrary `[animation:…]` utility. None of
 * index.css's twelve `@media (prefers-reduced-motion: reduce)` blocks named
 * `hqPulse`, so the dot kept pulsing with OS Reduce Motion on (#1857 Part B).
 *
 * The fix follows the same in-repo convention LogsFooter.jsx already uses
 * for its own arbitrary-utility pulses (heart-glow, donate-pop-in): append
 * `motion-reduce:[animation:none]` in the className itself, rather than a
 * plain CSS selector in index.css (that mechanism is for stable classes;
 * this dot, like the others, only has an inline Tailwind utility to hook).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import Header from '../components/Header';

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    minimize: vi.fn(async () => {}),
    toggleMaximize: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
  }),
}));

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
});

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Header mode="dub" setMode={() => {}} modelStatus="idle" />
    </QueryClientProvider>,
  );
}

describe('Header status dot — reduced motion', () => {
  it('opts the hqPulse animation out under prefers-reduced-motion', () => {
    const { container } = renderHeader();
    const dot = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('hqPulse'),
    );
    expect(dot, 'header status dot (hqPulse) not found').toBeTruthy();
    expect(dot.className).toMatch(/motion-reduce:\[animation:none\]/);
  });
});
