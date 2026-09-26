import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import {
  modelSectionKey,
  matchesModelQuery,
  groupModels,
} from '../components/settings/models/sections';
import { makeModelColumns } from '../components/settings/models/columns';

// ── Feature: grouped Model Store catalog with platform clarity ─────────────
// The flat table became role sections (TTS / ASR offline / Dictation /
// Diarisation). Curated rows (backend `curated`, from curated_on in
// models.yaml) wear a "recommended" chip; platform-incompatible rows
// (`supported: false`) collapse behind a per-section "Show incompatible (N)"
// toggle instead of rendering greyed-out inline.

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

const t = i18n.t.bind(i18n);

// ── Pure helpers ────────────────────────────────────────────────────────────

describe('modelSectionKey — role → catalog section', () => {
  it('maps TTS / plain ASR / Diarisation to their sections', () => {
    expect(modelSectionKey({ role: 'TTS' })).toBe('tts');
    expect(modelSectionKey({ role: 'ASR' })).toBe('asr');
    expect(modelSectionKey({ role: 'Diarisation' })).toBe('diarisation');
    expect(modelSectionKey({ role: 'diarization' })).toBe('diarisation'); // spelling alias
  });

  it('splits streaming/dictation ASR (engine sherpa-onnx OR a dictation tag) out of ASR', () => {
    expect(modelSectionKey({ role: 'ASR', engine: 'sherpa-onnx', tag: 'offline' })).toBe(
      'dictation',
    );
    expect(modelSectionKey({ role: 'ASR', tag: 'streaming' })).toBe('dictation');
    expect(modelSectionKey({ role: 'ASR' })).toBe('asr');
    // Only the documented dictation tags reroute (#1175 review): an unrelated
    // metadata tag must keep an offline-transcription model in ASR.
    expect(modelSectionKey({ role: 'ASR', tag: 'multilingual' })).toBe('asr');
    expect(modelSectionKey({ role: 'ASR', tag: 'recommended' })).toBe('asr');
  });

  it('routes unknown roles to "other" and never throws on malformed rows', () => {
    expect(modelSectionKey({ role: 'LLM' })).toBe('other');
    expect(modelSectionKey({})).toBe('other');
    expect(modelSectionKey(null)).toBe('other');
  });
});

describe('groupModels — ordered sections, query + compatibility split', () => {
  const MODELS = [
    { repo_id: 'd/diar', label: 'Diar', role: 'Diarisation' },
    { repo_id: 't/tts', label: 'Voice', role: 'TTS' },
    { repo_id: 'a/asr', label: 'Whisper', role: 'ASR', note: 'universal pick' },
    { repo_id: 's/dict', label: 'Parakeet', role: 'ASR', engine: 'sherpa-onnx', tag: 'offline' },
    { repo_id: 'x/mac-only', label: 'MacOnly', role: 'TTS', supported: false },
  ];

  it('groups into TTS → ASR → Dictation → Diarisation order, omitting empty sections', () => {
    const sections = groupModels(MODELS, '');
    expect(sections.map((s) => s.key)).toEqual(['tts', 'asr', 'dictation', 'diarisation']);
  });

  it('splits supported vs incompatible rows per section', () => {
    const tts = groupModels(MODELS, '').find((s) => s.key === 'tts');
    expect(tts.compatible.map((m) => m.repo_id)).toEqual(['t/tts']);
    expect(tts.incompatible.map((m) => m.repo_id)).toEqual(['x/mac-only']);
  });

  it('applies the search query over repo_id / label / note / role', () => {
    expect(groupModels(MODELS, 'parakeet').map((s) => s.key)).toEqual(['dictation']);
    expect(groupModels(MODELS, 'universal pick').map((s) => s.key)).toEqual(['asr']);
    expect(groupModels(MODELS, 'zzz')).toEqual([]);
    expect(matchesModelQuery(MODELS[2], '  WHISPER ')).toBe(true);
  });
});

// ── Curated "recommended" chip on catalog rows ──────────────────────────────

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

function renderNameCell(mOver = {}) {
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
  });
  const col = cols.find((c) => c.id === 'name');
  const m = { repo_id: 'org/model', label: 'My Model', role: 'asr', installed: false, ...mOver };
  return render(col.cell({ row: { original: m } }));
}

describe('Model Store row — curated "recommended" chip', () => {
  it('keeps repository metadata inline and moves the verbose note to its tooltip', () => {
    renderNameCell({ note: 'Long compatibility and quality guidance' });
    const repo = screen.getByText('org/model');
    expect(repo).toHaveClass('models-row__repo');
    expect(repo).toHaveAttribute('title', 'org/model · Long compatibility and quality guidance');
    expect(screen.queryByText('Long compatibility and quality guidance')).not.toBeInTheDocument();
  });

  it('badges a curated row with the recommended chip (existing badge_recommended key)', () => {
    renderNameCell({ curated: true });
    const chip = screen.getByTestId('model-recommended-org/model');
    expect(chip).toHaveTextContent(t('voicePanel.badge_recommended'));
    expect(chip).toHaveAttribute('title', t('models.recommended_title'));
  });

  it('a required row keeps its stronger "required" tag — no doubled chips', () => {
    renderNameCell({ curated: true, required: true });
    expect(screen.getByText(t('models.required_tag'))).toBeInTheDocument();
    expect(screen.queryByTestId('model-recommended-org/model')).not.toBeInTheDocument();
  });

  it('a non-curated row gets no chip', () => {
    renderNameCell({ curated: false });
    expect(screen.queryByTestId('model-recommended-org/model')).not.toBeInTheDocument();
  });
});

// ── Tab-level: sections + incompatible collapse ─────────────────────────────

const refetch = vi.fn();
const MODELS = [
  {
    repo_id: 'k2-fsa/OmniVoice',
    label: 'VoiceStudio TTS',
    role: 'TTS',
    size_gb: 2.4,
    installed: true,
    required: true,
  },
  {
    repo_id: 'Systran/faster-whisper-large-v3',
    label: 'Whisper large-v3',
    role: 'ASR',
    size_gb: 2.9,
    installed: false,
    curated: true,
  },
  {
    repo_id: 'csukuangfj/parakeet',
    label: 'Parakeet dictation',
    role: 'ASR',
    size_gb: 0.18,
    installed: false,
    engine: 'sherpa-onnx',
    tag: 'offline',
  },
  {
    repo_id: 'pyannote/speaker-diarization-3.1',
    label: 'Speaker diarization',
    role: 'Diarisation',
    size_gb: 0.3,
    installed: true,
  },
  {
    repo_id: 'mlx-community/whisper-large-v3-mlx',
    label: 'Whisper MLX',
    role: 'ASR',
    size_gb: 3.0,
    installed: false,
    supported: false,
    platforms: ['darwin-arm64'],
  },
];

vi.mock('../api/hooks', () => ({
  useModels: () => ({
    data: {
      models: MODELS,
      total_installed_bytes: 0,
      disk_free_gb: 42.5,
      hf_cache_dir: '/home/u/.cache/huggingface',
      platform_tags: ['linux', 'linux-x86_64', 'cuda'],
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
vi.mock('../api/system', () => ({
  listLoadedModels: vi.fn().mockResolvedValue({ models: [], count: 0 }),
  unloadLoadedModel: vi.fn(),
}));
vi.mock('../api/external', () => ({ openExternal: vi.fn() }));
vi.mock('../components/settings/VoicePreviewsPanel', () => ({ default: () => null }));
// Surface the per-section row set without the virtualizer (yields no rows in
// jsdom). Section chrome (headers, incompatible toggle) stays real.
vi.mock('../components/settings/models/ModelsTable', () => ({
  default: ({ tableRows }) => (
    <div data-testid="mock-table">
      {tableRows.map((r) => (
        <div key={r.id} data-testid="visible-row">
          {r.original.label}
        </div>
      ))}
    </div>
  ),
}));

import ModelStoreTab from '../components/settings/ModelStoreTab';

function mountTab(props = {}) {
  global.EventSource = class {
    constructor() {
      this.onmessage = null;
    }
    close() {}
  };
  return render(
    <I18nextProvider i18n={i18n}>
      <ModelStoreTab title="Leftovers" {...props} />
    </I18nextProvider>,
  );
}

const visibleLabels = () => screen.queryAllByTestId('visible-row').map((n) => n.textContent);

describe('Model Store — grouped catalog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders role sections in order with localized titles', async () => {
    mountTab();
    await waitFor(() => expect(screen.getByTestId('models-section-tts')).toBeInTheDocument());
    expect(screen.getByTestId('model-list-panel')).toBeInTheDocument();
    expect(screen.getByTestId('model-list-area')).toHaveClass('flex-1');
    const keys = ['tts', 'asr', 'dictation', 'diarisation'];
    const sections = keys.map((k) => screen.getByTestId(`models-section-${k}`));
    // DOM order matches the section order.
    for (let i = 1; i < sections.length; i++) {
      expect(
        sections[i - 1].compareDocumentPosition(sections[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(screen.getByText(t('models.section_dictation'))).toBeInTheDocument();
    expect(screen.getByText(t('models.section_asr'))).toBeInTheDocument();
  });

  it('routes sherpa-onnx rows to the Dictation section, plain ASR stays in ASR', async () => {
    mountTab();
    await waitFor(() => expect(screen.getByTestId('models-section-dictation')).toBeInTheDocument());
    const dictation = screen.getByTestId('models-section-dictation');
    expect(dictation).toHaveTextContent('Parakeet dictation');
    expect(dictation).not.toHaveTextContent('Whisper large-v3');
    const asr = screen.getByTestId('models-section-asr');
    expect(asr).toHaveTextContent('Whisper large-v3');
  });

  it('collapses incompatible rows behind a default-collapsed per-section toggle', async () => {
    mountTab();
    await waitFor(() => expect(screen.getByTestId('models-section-asr')).toBeInTheDocument());
    // The mac-only model is NOT rendered inline…
    expect(visibleLabels()).not.toContain('Whisper MLX');
    // …but the ASR section offers "Show incompatible (1)".
    const toggle = screen.getByTestId('models-incompatible-toggle-asr');
    expect(toggle).toHaveTextContent(t('models.show_incompatible', { count: 1 }));
    fireEvent.click(toggle);
    await waitFor(() => expect(visibleLabels()).toContain('Whisper MLX'));
    expect(toggle).toHaveTextContent(t('models.hide_incompatible', { count: 1 }));
    fireEvent.click(toggle);
    await waitFor(() => expect(visibleLabels()).not.toContain('Whisper MLX'));
  });

  it('sections without incompatible rows render no toggle', async () => {
    mountTab();
    await waitFor(() => expect(screen.getByTestId('models-section-tts')).toBeInTheDocument());
    expect(screen.queryByTestId('models-incompatible-toggle-tts')).not.toBeInTheDocument();
    expect(screen.queryByTestId('models-incompatible-toggle-dictation')).not.toBeInTheDocument();
  });
});

// ── Family scoping: the catalogue lists one family's weights under its engines ─

describe('Model Store — scoped to one engine family', () => {
  it('ASR carries offline ASR, streaming dictation and diarisation; TTS only TTS', async () => {
    mountTab({ family: 'asr' });
    await waitFor(() => expect(screen.getByTestId('models-section-asr')).toBeInTheDocument());
    expect(screen.getByTestId('models-section-dictation')).toBeInTheDocument();
    expect(screen.getByTestId('models-section-diarisation')).toBeInTheDocument();
    expect(screen.queryByTestId('models-section-tts')).not.toBeInTheDocument();
    expect(visibleLabels()).not.toContain('VoiceStudio TTS');
  });
});

describe('Model Store — only weights no engine owns', () => {
  it('drops rows an engine claims and keeps pipeline weights', async () => {
    MODELS[0].engines = ['omnivoice']; // VoiceStudio TTS now belongs to its engine
    try {
      const { unmount } = mountTab({ family: 'tts' });
      await new Promise((r) => setTimeout(r, 0));
      expect(screen.queryByTestId('models-section-tts')).not.toBeInTheDocument();
      expect(screen.queryByTestId('model-list-panel')).not.toBeInTheDocument();
      unmount();
    } finally {
      delete MODELS[0].engines;
    }
    mountTab({ family: 'asr' });
    await waitFor(() =>
      expect(screen.getByTestId('models-section-diarisation')).toBeInTheDocument(),
    );
    expect(screen.getByRole('heading', { name: 'Leftovers' })).toBeInTheDocument();
  });
});
