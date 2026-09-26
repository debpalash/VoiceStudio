import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import { makeModelColumns } from '../components/settings/models/columns';

// ── Feature: memory-residency indicators in the Model Store ────────────────
// GET /model/loaded knows which models are resident in RAM/VRAM right now
// (checkpoint == repo_id for HF-cached models). The store marks those rows
// "In memory" and offers Unload where the entry is unloadable — freeing
// memory is safe by contract (the model reloads lazily on next use).

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

const t = i18n.t.bind(i18n);
const REPO = 'openbmb/VoxCPM2';

const IDLE_RT = {
  showBar: false,
  isDeleting: false,
  isInstalling: false,
  rowBusy: false,
  unsupported: false,
  aggPct: null,
  totals: { downloaded: 0, total: 0 },
  hasFiles: false,
};

function renderCell(colId, overrides = {}, mOver = {}) {
  const cols = makeModelColumns({
    t,
    getRowRuntime: () => IDLE_RT,
    speedRef: { current: {} },
    MODEL_ROLE_LABEL: {},
    onInstall: vi.fn(),
    onDelete: vi.fn(),
    onReinstall: vi.fn(),
    onCancel: vi.fn(),
    onDismissError: vi.fn(),
    ...overrides,
  });
  const col = cols.find((c) => c.id === colId);
  const m = {
    repo_id: REPO,
    label: 'VoxCPM2',
    role: 'tts',
    size_gb: 3.4,
    installed: true,
    ...mOver,
  };
  return render(col.cell({ row: { original: m } }));
}

const RESIDENT = {
  id: 'tts',
  name: 'VoiceStudio TTS',
  checkpoint: REPO,
  device: 'mps',
  vram_mb: 2048,
  unloadable: true,
  engine_id: 'omnivoice',
  is_active_engine: true,
};

describe('Model Store — residency column rendering', () => {
  it('shows an "In memory" badge next to installed when the model is resident', () => {
    renderCell('status', { getResidency: () => RESIDENT });
    expect(screen.getByText(t('models.installed'))).toBeInTheDocument();
    const chip = screen.getByTestId(`model-resident-${REPO}`);
    expect(chip).toHaveTextContent('In memory');
    // The affordance explains itself: unloading is safe.
    expect(chip).toHaveAttribute('title', t('models.in_memory_title'));
  });

  it('shows no residency badge when the model is not resident', () => {
    renderCell('status', { getResidency: () => null });
    expect(screen.getByText(t('models.installed'))).toBeInTheDocument();
    expect(screen.queryByTestId(`model-resident-${REPO}`)).not.toBeInTheDocument();
  });

  it('renders exactly as before for legacy callers that pass no getResidency', () => {
    renderCell('status');
    expect(screen.getByText(t('models.installed'))).toBeInTheDocument();
    expect(screen.queryByTestId(`model-resident-${REPO}`)).not.toBeInTheDocument();
  });

  it('offers Unload in the actions cell for a resident, unloadable model', () => {
    const onUnload = vi.fn();
    renderCell('actions', { getResidency: () => RESIDENT, onUnload });
    fireEvent.click(screen.getByRole('button', { name: `Unload ${REPO} from memory` }));
    expect(onUnload).toHaveBeenCalledWith(REPO);
  });

  it('offers no Unload when the loaded entry is not unloadable', () => {
    const onUnload = vi.fn();
    renderCell('actions', {
      getResidency: () => ({ ...RESIDENT, unloadable: false }),
      onUnload,
    });
    expect(screen.queryByRole('button', { name: /unload/i })).not.toBeInTheDocument();
  });

  it('offers no Unload for a non-resident model', () => {
    renderCell('actions', { getResidency: () => null, onUnload: vi.fn() });
    expect(screen.queryByRole('button', { name: /unload/i })).not.toBeInTheDocument();
  });
});

// ── Tab-level wiring: /model/loaded → residency map → unload round-trip ────

const listLoadedModels = vi.fn();
const unloadLoadedModel = vi.fn();
vi.mock('../api/system', () => ({
  listLoadedModels: (...a) => listLoadedModels(...a),
  unloadLoadedModel: (...a) => unloadLoadedModel(...a),
}));

const refetch = vi.fn();
vi.mock('../api/hooks', () => ({
  useModels: () => ({
    data: {
      models: [
        {
          repo_id: 'openbmb/VoxCPM2',
          label: 'VoxCPM2',
          role: 'tts',
          size_gb: 3.4,
          installed: true,
          size_on_disk_bytes: 3.4 * 1024 ** 3,
        },
      ],
      total_installed_bytes: 0,
      disk_free_gb: 42.5,
      hf_cache_dir: '/home/u/.cache/huggingface',
    },
    isLoading: false,
    refetch,
  }),
  useRecommendations: () => ({ data: null, refetch }),
  useInstallModel: () => ({ mutateAsync: vi.fn() }),
  useDeleteModel: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('../api/setup', () => ({
  setupDownloadStreamUrl: () => 'http://localhost/stream',
  cancelInstallModel: vi.fn(),
}));
vi.mock('../api/external', () => ({ openExternal: vi.fn() }));
vi.mock('../components/settings/models/RecoBanner', () => ({ default: () => null }));
vi.mock('../components/settings/VoicePreviewsPanel', () => ({ default: () => null }));
// Render every row's cells for real (the virtualizer yields nothing in jsdom).
vi.mock('../components/settings/models/ModelsTable', () => ({
  default: ({ tableRows }) => (
    <div>
      {tableRows.map((r) => (
        <div key={r.id} data-testid={`rendered-row-${r.id}`}>
          {r.getVisibleCells().map((c) => (
            <MockCell key={c.id} cell={c} />
          ))}
        </div>
      ))}
    </div>
  ),
}));

// Tiny helper the ModelsTable mock uses to flex-render real column cells.
function MockCell({ cell }) {
  const def = cell.column.columnDef.cell;
  return typeof def === 'function' ? def(cell.getContext()) : (def ?? null);
}

import ModelStoreTab from '../components/settings/ModelStoreTab';

function mountTab() {
  global.EventSource = class {
    constructor() {
      this.onmessage = null;
    }
    close() {}
  };
  return render(
    <I18nextProvider i18n={i18n}>
      <ModelStoreTab info={{ has_hf_token: true }} modelBadge={null} />
    </I18nextProvider>,
  );
}

describe('Model Store — residency wiring through /model/loaded', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('maps a loaded checkpoint to its row and unloads by the loaded id', async () => {
    let loaded = { models: [RESIDENT], count: 1 };
    listLoadedModels.mockImplementation(async () => loaded);
    unloadLoadedModel.mockImplementation(async () => {
      loaded = { models: [], count: 0 };
      return { unloaded: 'tts', success: true };
    });
    mountTab();

    await waitFor(() => {
      expect(screen.getByTestId('model-resident-openbmb/VoxCPM2')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Unload openbmb/VoxCPM2 from memory' }));
    await waitFor(() => {
      // The /model/loaded id ('tts'), never the repo id.
      expect(unloadLoadedModel).toHaveBeenCalledWith('tts');
    });
    await waitFor(() => {
      expect(screen.queryByTestId('model-resident-openbmb/VoxCPM2')).not.toBeInTheDocument();
    });
  });

  it('renders the store normally when the residency probe fails (advisory only)', async () => {
    listLoadedModels.mockRejectedValue(new Error('backend restarting'));
    mountTab();
    await waitFor(() => {
      expect(screen.getByText('VoxCPM2')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('model-resident-openbmb/VoxCPM2')).not.toBeInTheDocument();
  });
});
