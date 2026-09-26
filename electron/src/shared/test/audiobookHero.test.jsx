import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import AudiobookHero from '../components/audiobook/AudiobookHero';

const t = (key) => key;

// AudiobookHero mounts EngineQuickSwitch, whose useEngines query needs a client.
// One client at module scope: an inline `new QueryClient()` would be a fresh
// client on every wrapper render, so a rerender() drops the query cache.
const queryClient = new QueryClient();
const wrapper = ({ children }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

function renderHero(overrides = {}) {
  return render(
    <AudiobookHero
      t={t}
      busy={false}
      importing={false}
      planLoading={false}
      generating={false}
      canRun={false}
      onImport={vi.fn()}
      onLoadSample={vi.fn()}
      onPreview={vi.fn()}
      onCreate={vi.fn()}
      onStop={vi.fn()}
      {...overrides}
    />,
    { wrapper },
  );
}

describe('AudiobookHero', () => {
  it('opens the manuscript picker from a focusable import button', () => {
    const { container } = renderHero();
    const button = screen.getByRole('button', { name: 'audiobook.import' });
    const input = container.querySelector('input[type="file"]');
    const click = vi.spyOn(input, 'click');

    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);

    expect(click).toHaveBeenCalledOnce();
    expect(input).toHaveAttribute('accept', '.txt,.md,.epub,.pdf');
  });
});
