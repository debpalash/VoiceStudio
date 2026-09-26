import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Header from '../components/Header';

function renderHeader(modelStatus) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(['sysinfo'], {});
  queryClient.setQueryData(['engines'], {});
  return render(
    <QueryClientProvider client={queryClient}>
      <Header mode="audiobook" setMode={() => {}} modelStatus={modelStatus} />
    </QueryClientProvider>,
  );
}

describe('Header wave bars (#1911)', () => {
  it('animate for actual loading but remain still when the model is merely ready', () => {
    const ready = renderHeader('ready');
    expect(ready.getByTestId('header-wave-bars').querySelector('[class*="hqBounce"]')).toBeNull();
    ready.unmount();

    const loading = renderHeader('loading');
    expect(
      loading.getByTestId('header-wave-bars').querySelector('[class*="hqBounce"]'),
    ).toBeTruthy();
  });
});
