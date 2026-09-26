import { render, screen, within } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import DubHeader from './DubHeader';

// DubHeader mounts EngineQuickSwitch, whose useEngines query needs a client.
// One client at module scope: an inline `new QueryClient()` would be a fresh
// client on every wrapper render, so a rerender() drops the query cache.
const queryClient = new QueryClient();
const wrapper = ({ children }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

describe('DubHeader command bar', () => {
  it('keeps project identity, compact pipeline, and batch action in one production bar', () => {
    render(
      <I18nextProvider i18n={i18n}>
        <DubHeader
          t={i18n.t.bind(i18n)}
          dubFilename="trying the WORLD'S SMALLEST curling iron."
          dubDuration={63.6}
          dubSegments={Array.from({ length: 14 }, (_, id) => ({ id: String(id) }))}
          activeProjectName=""
          saveProject={vi.fn()}
          resetDub={vi.fn()}
          dubStep="editing"
          handleDubStop={vi.fn()}
          dubProgress={{ current: 0, total: 14 }}
          onGenerateClick={vi.fn()}
          isTranslating={false}
          multiLangMode
          multiLangs={Array.from({ length: 16 }, (_, id) => ({ code: `l${id}` }))}
          incrementalPlan={null}
          handleDubGenerate={vi.fn()}
          qcRunning={false}
          handleDubQc={vi.fn()}
          setExportOpen={vi.fn()}
          pipelineSteps={[]}
          onPipelineStep={vi.fn()}
        />
      </I18nextProvider>,
      { wrapper },
    );

    const bar = screen.getByTestId('dub-command-bar');
    expect(within(bar).getByText("trying the WORLD'S SMALLEST curling iron.")).toBeInTheDocument();
    expect(within(bar).getByText('1:03.6')).toBeInTheDocument();
    expect(within(bar).getByText(/14 segs/i)).toBeInTheDocument();
    expect(within(bar).getByRole('list', { name: 'Dubbing pipeline' })).toHaveClass(
      'dub-stepper--command',
    );
    expect(within(bar).getByRole('button', { name: /Generate 16 dubs/i })).toBeInTheDocument();
  });
});
