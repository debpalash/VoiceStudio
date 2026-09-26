import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { fireEvent, render, screen, act } from '@testing-library/react';
import i18n from '../i18n';

import DubFooter from '../components/dub/DubFooter';

const t = i18n.t.bind(i18n);

function makeProps(over = {}) {
  return {
    t,
    dubStep: 'editing',
    dubTracks: [],
    incrementalPlan: null,
    dubError: 'TRANSLATION FAILED: 400 — deep_translator not installed',
    dubFailure: null,
    onDismissError: vi.fn(),
    exportTracks: {},
    setExportTracks: vi.fn(),
    dubSegments: [],
    translateQuality: 'fast',
    ...over,
  };
}

describe('DubFooter — dismissable / auto-clearing translation error banner', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders a dismiss button that clears the error (× → onDismissError)', () => {
    const onDismissError = vi.fn();
    render(<DubFooter {...makeProps({ onDismissError })} />);
    expect(screen.getByText(/TRANSLATION FAILED/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: t('dub.dismiss_error') }));
    expect(onDismissError).toHaveBeenCalledTimes(1);
  });

  it('auto-clears the banner after the timeout while editing', () => {
    vi.useFakeTimers();
    const onDismissError = vi.fn();
    render(<DubFooter {...makeProps({ onDismissError, dubStep: 'editing' })} />);
    expect(onDismissError).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(12000);
    });
    expect(onDismissError).toHaveBeenCalledTimes(1);
  });

  it('does NOT auto-clear while generating (live per-segment errors must persist)', () => {
    vi.useFakeTimers();
    const onDismissError = vi.fn();
    render(<DubFooter {...makeProps({ onDismissError, dubStep: 'generating' })} />);
    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(onDismissError).not.toHaveBeenCalled();
    // …but the × is still available for a manual dismiss.
    fireEvent.click(screen.getByRole('button', { name: t('dub.dismiss_error') }));
    expect(onDismissError).toHaveBeenCalledTimes(1);
  });

  it('no banner, no dismiss button when there is no error', () => {
    render(<DubFooter {...makeProps({ dubError: '' })} />);
    expect(screen.queryByRole('button', { name: t('dub.dismiss_error') })).not.toBeInTheDocument();
  });
});
