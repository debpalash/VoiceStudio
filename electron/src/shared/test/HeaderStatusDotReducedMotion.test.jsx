// The status dot in the header animates via an arbitrary Tailwind utility
// (keyframes hqPulse). Global prefers-reduced-motion CSS cannot target that,
// so the element has to carry its own motion-reduce override.
import React from 'react';
import { it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Header from '../components/Header';

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    minimize: async () => {},
    toggleMaximize: async () => {},
    close: async () => {},
  }),
}));

it('pulsing dot opts out of its animation under reduced motion', () => {
  const client = new QueryClient();
  client.setQueryData(['sysinfo'], {});
  const { container } = render(
    <QueryClientProvider client={client}>
      <Header mode="dub" setMode={() => {}} modelStatus="idle" />
    </QueryClientProvider>,
  );

  const pulsing = container.querySelectorAll('[class*="hqPulse"]');
  expect(pulsing).toHaveLength(1);
  const tokens = pulsing[0].className.split(/\s+/);
  expect(tokens).toContain('motion-reduce:[animation:none]');
});
