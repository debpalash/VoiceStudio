import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import DubFooter from './DubFooter';

describe('dubbing error notice', () => {
  it('shows a recurring failure after a dismissed error has been cleared', () => {
    const props = {
      t: (key) => key,
      dubStep: 'generating',
      dubTracks: [],
      dubSegments: [],
      dubError: 'Generation failed',
      onDismissError: vi.fn(),
    };
    const { rerender } = render(<DubFooter {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'dub.dismiss_error' }));
    expect(screen.queryByTestId('dub-error-notice')).not.toBeInTheDocument();

    rerender(<DubFooter {...props} dubError={null} />);
    rerender(<DubFooter {...props} />);
    expect(screen.getByTestId('dub-error-message')).toHaveTextContent('Generation failed');
  });

  it('keeps all segment errors in a wrapping, height-bounded region with dismissal outside it', () => {
    const onDismissError = vi.fn();
    const message = Array.from(
      { length: 100 },
      (_, i) => `Seg ${i}: Generation failed. ${'x'.repeat(100)}`,
    ).join(' ');
    render(
      <DubFooter
        t={(key) => key}
        dubStep="generating"
        dubTracks={[]}
        dubSegments={[]}
        dubError={message}
        onDismissError={onDismissError}
      />,
    );
    const text = screen.getByTestId('dub-error-message');
    expect(text).toHaveTextContent(message);
    expect(text).toHaveClass('min-w-0', 'max-h-28', 'overflow-y-auto', '[overflow-wrap:anywhere]');
    const dismiss = screen.getByRole('button', { name: 'dub.dismiss_error' });
    expect(text).not.toContainElement(dismiss);
    const collapse = screen.getByRole('button', { name: 'common.error' });
    fireEvent.click(collapse);
    expect(screen.queryByTestId('dub-error-message')).not.toBeInTheDocument();
    expect(collapse).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(collapse);
    expect(screen.getByTestId('dub-error-message')).toHaveTextContent(message);
    fireEvent.click(dismiss);
    expect(onDismissError).toHaveBeenCalledOnce();
    expect(screen.queryByTestId('dub-error-notice')).not.toBeInTheDocument();
  });
});
