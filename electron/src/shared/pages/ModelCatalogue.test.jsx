// Model Catalogue workspace — one family axis, deep-link hand-off, persistence.
//
// The summary, engine list and weights list are mounted stand-ins for the real
// components (each has its own suite and hits the network); this suite is
// about the page around them: one family drives both lists, LLM has no
// weights, Change on the summary switches the family, and the old two-pane
// switch is gone.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';

vi.mock('../components/settings/EnginesTab', () => ({
  default: ({ initialFamily, onFamilyChange, catalogueLayout }) => (
    <div data-testid="stub-engines" data-catalogue-layout={catalogueLayout || undefined}>
      {initialFamily}
      <button type="button" onClick={() => onFamilyChange('llm')}>
        to-llm
      </button>
    </div>
  ),
}));
vi.mock('../components/settings/ModelStoreTab', () => ({
  default: ({ family }) => <div data-testid="stub-weights">{family}</div>,
}));
vi.mock('../components/catalogue/SetupSummary', () => ({
  default: ({ onChange }) => (
    <div data-testid="stub-summary">
      <button type="button" onClick={() => onChange('asr')}>
        change-asr
      </button>
    </div>
  ),
}));
vi.mock('../api/hooks', () => ({
  useModels: () => ({
    data: {
      total_installed_bytes: 3 * 1024 ** 3,
      disk_free_gb: 41,
      hf_cache_dir: '/Users/dev/.cache/huggingface',
    },
  }),
}));

import { useAppStore } from '../store';
import ModelCatalogue from './ModelCatalogue';

describe('ModelCatalogue', () => {
  beforeEach(() => {
    localStorage.clear();
    act(() => {
      useAppStore.setState({ mode: 'catalogue', pendingCatalogueFamily: null });
    });
  });

  it('is one page: summary, then the TTS engines and TTS weights, no pane switch', () => {
    render(<ModelCatalogue />);
    expect(screen.getByTestId('stub-summary')).toBeInTheDocument();
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('tts');
    expect(screen.getByTestId('stub-engines')).toHaveAttribute('data-catalogue-layout', 'true');
    expect(screen.getByTestId('stub-weights')).toHaveTextContent('tts');
    expect(screen.queryByRole('tab')).toBeNull();
    // Reading order: summary above engines above weights.
    const [summary, engines, weights] = ['stub-summary', 'stub-engines', 'stub-weights'].map((id) =>
      screen.getByTestId(id),
    );
    expect(
      summary.compareDocumentPosition(engines) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      engines.compareDocumentPosition(weights) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('Change on the summary switches the family for engines and weights together', () => {
    render(<ModelCatalogue />);
    fireEvent.click(screen.getByText('change-asr'));
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('asr');
    expect(screen.getByTestId('stub-weights')).toHaveTextContent('asr');
  });

  it('LLM engines bring their own weights, so the weights block hides — but stays mounted', () => {
    render(<ModelCatalogue />);
    fireEvent.click(screen.getByText('to-llm'));
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('llm');
    // Hidden, not unmounted: the model store keeps its download-progress
    // state across family switches instead of losing it mid-download.
    expect(screen.getByTestId('catalogue-weights')).not.toBeVisible();
    expect(screen.getByTestId('stub-weights')).toBeInTheDocument();
    fireEvent.click(screen.getByText('change-asr'));
    expect(screen.getByTestId('catalogue-weights')).toBeVisible();
  });

  it('uses the wide workspace shell for data-heavy lists', () => {
    render(<ModelCatalogue />);
    expect(screen.getByTestId('model-catalogue').firstElementChild).toHaveClass('max-w-[1500px]');
  });

  it('remembers the family across visits', () => {
    const { unmount } = render(<ModelCatalogue />);
    fireEvent.click(screen.getByText('change-asr'));
    unmount();
    render(<ModelCatalogue />);
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('asr');
  });

  it('honours a pending family deep-link on first paint and clears it', () => {
    act(() => useAppStore.setState({ pendingCatalogueFamily: 'llm' }));
    render(<ModelCatalogue />);
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('llm');
    expect(useAppStore.getState().pendingCatalogueFamily).toBeNull();
  });

  it('switches family when a deep-link arrives while already mounted', () => {
    render(<ModelCatalogue />);
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('tts');
    act(() => {
      useAppStore.getState().openCatalogue('asr');
    });
    expect(screen.getByTestId('stub-engines')).toHaveTextContent('asr');
    expect(useAppStore.getState().pendingCatalogueFamily).toBeNull();
  });

  it('shows one storage line and links it to Settings → Storage', () => {
    render(<ModelCatalogue />);
    const footer = screen.getByTestId('catalogue-storage');
    expect(footer).toHaveTextContent('41 GB');
    expect(footer).toHaveTextContent('~/.cache/huggingface');
    fireEvent.click(screen.getByTestId('catalogue-storage-link'));
    expect(useAppStore.getState().mode).toBe('settings');
    expect(useAppStore.getState().pendingSettingsTab).toBe('storage');
  });
});

describe('openCatalogue', () => {
  beforeEach(() => {
    act(() => useAppStore.setState({ mode: 'studio', pendingCatalogueFamily: null }));
  });

  it('navigates to the catalogue on the requested family', () => {
    act(() => useAppStore.getState().openCatalogue('llm'));
    expect(useAppStore.getState().mode).toBe('catalogue');
    expect(useAppStore.getState().pendingCatalogueFamily).toBe('llm');
  });

  it('accepts the object form and ignores the retired pane key', () => {
    act(() => useAppStore.getState().openCatalogue({ pane: 'models', family: 'asr' }));
    expect(useAppStore.getState().mode).toBe('catalogue');
    expect(useAppStore.getState().pendingCatalogueFamily).toBe('asr');
  });

  it('defaults to the last family when called bare', () => {
    act(() => useAppStore.getState().openCatalogue());
    expect(useAppStore.getState().mode).toBe('catalogue');
    expect(useAppStore.getState().pendingCatalogueFamily).toBeNull();
  });
});
