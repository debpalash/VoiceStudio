import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Header from '../components/Header';

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    minimize: vi.fn(async () => {}),
    toggleMaximize: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
  }),
}));

afterEach(() => {
  cleanup();
  delete window.__TAURI_INTERNALS__;
});

const mountHeader = (modelStatus) => {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queries.setQueryData(['sysinfo'], { ram: 2, total_ram: 8, cpu: 5 });
  queries.setQueryData(['engines'], { tts: { active: 'demo', backends: [] } });
  const result = render(
    <QueryClientProvider client={queries}>
      <Header mode="projects" setMode={vi.fn()} modelStatus={modelStatus} />
    </QueryClientProvider>,
  );
  return result.container.querySelector('[data-testid="header-wave-bars"]');
};

describe('model activity bars', () => {
  it.each([
    ['ready', false],
    ['loading', true],
  ])('animation for %s is %s', (status, expected) => {
    const wave = mountHeader(status);
    const classes = [...wave.children].map((bar) => bar.className).join(' ');
    expect(classes.includes('hqBounce')).toBe(expected);
    expect(wave.className.includes('opacity-100')).toBe(expected);
  });
});
