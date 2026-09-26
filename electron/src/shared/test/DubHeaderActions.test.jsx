import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../i18n';

import DubHeader from '../components/dub/DubHeader';

const t = i18n.t.bind(i18n);

// DubHeader mounts EngineQuickSwitch, whose useEngines query needs a client.
// One client at module scope: an inline `new QueryClient()` would be a fresh
// client on every wrapper render, so a rerender() drops the query cache.
const queryClient = new QueryClient();
const wrapper = ({ children }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

function makeProps(overrides = {}) {
  return {
    t,
    dubFilename: 'sample.mp4',
    dubDuration: 42,
    dubSegments: [{ id: 'segment-1' }],
    activeProjectName: '',
    saveProject: vi.fn(),
    resetDub: vi.fn(),
    dubStep: 'done',
    handleDubStop: vi.fn(),
    dubProgress: { current: 0, total: 0 },
    onGenerateClick: vi.fn(),
    isTranslating: false,
    multiLangMode: false,
    multiLangs: [],
    incrementalPlan: { stale: [], fresh: [] },
    handleDubGenerate: vi.fn(),
    qcRunning: false,
    handleDubQc: vi.fn(),
    setExportOpen: vi.fn(),
    pipelineSteps: [],
    onPipelineStep: vi.fn(),
    ...overrides,
  };
}

describe('DubHeader — polished workflow actions', () => {
  it('keeps all three actions visible, ordered, accessible, and wired', () => {
    const props = makeProps();
    render(<DubHeader {...props} />, { wrapper });

    const group = screen.getByTestId('dub-primary-actions');
    const buttons = within(group).getAllByRole('button');
    const generate = within(group).getByRole('button', { name: t('dub.generate_dub') });
    const verify = within(group).getByRole('button', { name: t('dub.qc_btn') });
    const exportButton = within(group).getByRole('button', { name: t('dub.export_btn') });

    expect(buttons).toEqual([generate, verify, exportButton]);
    expect(generate).toHaveTextContent(t('dub.generate_dub'));
    expect(verify).toHaveTextContent(t('dub.verify'));
    expect(exportButton).toHaveTextContent(t('dub.export_btn'));
    expect(generate).toHaveClass('dub-action-btn--generate');
    expect(verify).toHaveClass('dub-action-btn--verify');
    expect(exportButton).toHaveClass('dub-action-btn--export');
    expect(group.querySelectorAll('svg[aria-hidden="true"]')).toHaveLength(3);
    for (const button of buttons) {
      expect(button).toHaveAttribute('type', 'button');
      expect(button).toHaveClass('focus-visible:shadow-[var(--focus-ring)]');
    }

    fireEvent.click(generate);
    fireEvent.click(verify);
    fireEvent.click(exportButton);
    expect(props.onGenerateClick).toHaveBeenCalledOnce();
    expect(props.handleDubQc).toHaveBeenCalledOnce();
    expect(props.setExportOpen).toHaveBeenCalledWith(true);
  });

  it('wraps and stretches actions in mini shells without changing their labels', () => {
    render(<DubHeader {...makeProps()} />, { wrapper });

    const group = screen.getByTestId('dub-primary-actions');
    expect(group).toHaveClass('flex-wrap', '[.shell-mini_&]:w-full');
    for (const button of within(group).getAllByRole('button')) {
      expect(button).toHaveClass('[.shell-mini_&]:flex-1!', 'motion-reduce:transform-none');
    }
  });

  it('exposes verification progress and preserves disabled action guards', () => {
    const { rerender } = render(<DubHeader {...makeProps({ qcRunning: true })} />, { wrapper });
    const verify = screen.getByRole('button', { name: t('dub.qc_btn') });
    expect(verify).toBeDisabled();
    expect(verify).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: t('dub.generate_dub') })).toBeDisabled();
    expect(screen.getByRole('button', { name: t('dub.export_btn') })).toBeDisabled();

    rerender(
      <DubHeader {...makeProps({ dubStep: 'editing', dubSegments: [], incrementalPlan: null })} />,
    );
    expect(screen.getByRole('button', { name: t('dub.generate_dub') })).toBeDisabled();
    expect(screen.getByRole('button', { name: t('dub.export_btn') })).toBeDisabled();
    expect(screen.queryByRole('button', { name: t('dub.qc_btn') })).not.toBeInTheDocument();
  });
});
