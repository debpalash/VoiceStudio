import React from 'react';
import i18next from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

// Mock the toast import the component depends on — keeps the test free
// of side-effect side-channels (toast() schedules timers we don't want).
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: { error: vi.fn(), success: vi.fn() },
}));

// Residency layer defaults (/model/loaded) — mocked so tests that don't
// inject apiListLoadedModels never hit the network. Residency tests inject
// their own.
vi.mock('../api/system', () => ({
  listLoadedModels: vi.fn().mockResolvedValue({ models: [], count: 0 }),
  unloadLoadedModel: vi.fn(),
}));

import EngineCompatibilityMatrix, {
  FORCE_WAIT_TIMEOUT_MS,
  fmtDiskBytes,
} from '../components/EngineCompatibilityMatrix';

/** Build a minimal AllEnginesResponse with the three rows the plan calls for. */
function makeEnginesResponse({ inProcessAvailable = true, inProcessHasLastError = false } = {}) {
  return {
    tts: {
      active: 'omnivoice',
      backends: [
        {
          id: 'omnivoice',
          display_name: 'OmniVoice (test)',
          available: inProcessAvailable,
          reason: inProcessAvailable ? null : 'omnivoice package missing',
          install_hint: 'pip install omnivoice',
          last_error: inProcessHasLastError ? 'previous load failed' : null,
          isolation_mode: 'in-process',
          gpu_compat: ['cuda', 'mps', 'cpu'],
        },
        {
          id: 'kittentts',
          display_name: 'KittenTTS (test)',
          available: false,
          reason: 'kittentts not installed',
          install_hint: 'pip install kittentts',
          last_error: 'auth failed for hf_***REDACTED***',
          isolation_mode: 'in-process',
          gpu_compat: ['cpu'],
        },
        {
          id: 'indextts2',
          display_name: 'IndexTTS2 (test)',
          available: true,
          reason: null,
          install_hint: 'git clone …',
          last_error: null,
          isolation_mode: 'subprocess',
          gpu_compat: ['cuda', 'mps', 'cpu'],
        },
      ],
    },
    asr: { active: 'whisperx', backends: [] },
    llm: { active: 'off', backends: [] },
  };
}

// ── DOM helpers for the list + detail layout ──────────────────────────────
// One <tr data-engine-id> per engine; the engine name is a details toggle
// (`why-toggle-<id>`) that selects the row and opens `engine-detail-<id>`.
const rowOf = (id) => document.querySelector(`[data-engine-id="${id}"]`);
const nameOf = (id) => screen.getByTestId(`why-toggle-${id}`);
const openDetails = (id) => {
  fireEvent.click(nameOf(id));
  return screen.getByTestId(`engine-detail-${id}`);
};
const waitForRow = (id) => screen.findByTestId(`why-toggle-${id}`);

describe('EngineCompatibilityMatrix', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('formats disk sizes with the active locale', () => {
    expect(fmtDiskBytes(1536 * 1024 * 1024, 'unknown', 'en')).toBe('1.50 GB');
    expect(fmtDiskBytes(null, 'unknown', 'en')).toBe('unknown');
  });

  it('lists available engines first, keeping registration order inside each group', async () => {
    const response = makeEnginesResponse();
    response.tts.backends = [
      response.tts.backends[1], // kittentts — unavailable, registered first
      response.tts.backends[0], // omnivoice — available
      { ...response.tts.backends[2], available: false }, // indextts2 — unavailable
    ];
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(response)}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    const ids = [...document.querySelectorAll('[data-engine-id]')].map((el) =>
      el.getAttribute('data-engine-id'),
    );
    expect(ids).toEqual(['omnivoice', 'kittentts', 'indextts2']);
    // Two captions frame the groups: what you can use, then what to add.
    expect(screen.getByText('Ready to use')).toBeInTheDocument();
    expect(screen.getByText('Add more engines')).toBeInTheDocument();
  });

  it('renders a semantic table: one row per backend under three columns and an action column', async () => {
    const apiListEngines = vi.fn().mockResolvedValue(makeEnginesResponse());
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    expect(apiListEngines).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(document.querySelectorAll('[data-engine-id]').length).toBe(3);
    expect(screen.getByText('KittenTTS (test)')).toBeInTheDocument();
    expect(screen.getByText('IndexTTS2 (test)')).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader').map((el) => el.textContent)).toEqual([
      'Engine',
      'Runs on',
      'Status',
      'Actions',
    ]);
    // Nothing selected yet: the detail slot carries the hint, not a panel.
    expect(screen.getByTestId('engine-detail-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('engine-detail-omnivoice')).not.toBeInTheDocument();
  });

  it('keeps every row to one line: name, device, status phrase, one primary action', async () => {
    const response = makeEnginesResponse();
    response.tts.backends[0] = {
      ...response.tts.backends[0],
      effective_device: 'cuda',
      routing_status: 'accelerated',
    };
    render(
      <EngineCompatibilityMatrix
        family="tts"
        onSelect={vi.fn()}
        apiListEngines={vi.fn().mockResolvedValue(response)}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    // Active row: device + status, no button (nothing to do).
    const omni = rowOf('omnivoice');
    expect(within(omni).getByTestId('runs-on-omnivoice')).toHaveTextContent('CUDA');
    expect(within(omni).getByText('GPU active')).toBeInTheDocument();
    expect(within(omni).getByText('active')).toBeInTheDocument();
    expect(within(omni).queryByRole('button', { name: /use omnivoice/i })).not.toBeInTheDocument();
    // Available, not active: exactly one primary action — Use.
    const index = rowOf('indextts2');
    expect(within(index).getByRole('button', { name: /use indextts2/i })).toBeInTheDocument();
    expect(
      within(index).queryByRole('button', { name: /test indextts2/i }),
    ).not.toBeInTheDocument();
    // Unavailable, not installable: no button at all; the status says why.
    const kitten = rowOf('kittentts');
    expect(within(kitten).queryAllByRole('button')).toHaveLength(1); // the name toggle only
    expect(within(kitten).getByText('Needs setup')).toBeInTheDocument();
    // Compat chips, isolation and hints are NOT on the row any more.
    expect(within(omni).queryByText('MPS')).not.toBeInTheDocument();
    expect(within(index).queryByText(/runs isolated/i)).not.toBeInTheDocument();
    expect(within(omni).queryByText('pip install omnivoice')).not.toBeInTheDocument();
  });

  it("dims an unavailable engine's name while its status stays legible", async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('kittentts');
    expect(nameOf('kittentts')).toHaveClass('text-muted-foreground');
    expect(nameOf('omnivoice')).toHaveClass('text-foreground');
    expect(within(rowOf('kittentts')).getByText('Needs setup')).toHaveClass(
      'text-muted-foreground',
    );
  });

  it('shows separate estimates and measured disk categories on demand', async () => {
    const response = makeEnginesResponse();
    response.tts.backends[0].disk_usage = {
      estimate: {
        model_download_bytes: 2 * 1024 ** 3,
        package_download_bytes: null,
        unique_installed_bytes: 3 * 1024 ** 3,
        potentially_shared_bytes: null,
        temporary_free_bytes: 4 * 1024 ** 3,
        destination: 'hf_model_cache',
        confidence: 'estimated',
        deduplication: null,
      },
      actual: {},
    };
    const apiGetDiskUsage = vi.fn().mockResolvedValue({
      estimate: response.tts.backends[0].disk_usage.estimate,
      actual: {
        model_bytes: 1024 ** 3,
        environment_bytes: null,
        cache_bytes: 0,
        total_owned_bytes: 1024 ** 3,
        confidence: 'measured',
      },
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(response)}
        apiGetEngineHealth={vi.fn()}
        apiGetDiskUsage={apiGetDiskUsage}
      />,
    );
    await waitForRow('omnivoice');
    const panel = openDetails('omnivoice');
    fireEvent.click(within(panel).getByRole('button', { name: 'Disk details' }));
    await waitFor(() => expect(apiGetDiskUsage).toHaveBeenCalledWith('omnivoice'));
    const details = await screen.findByTestId('disk-usage-omnivoice');
    expect(within(details).getByText('Model download')).toBeInTheDocument();
    expect(within(details).getByText('Package download')).toBeInTheDocument();
    expect(within(details).getByText('Actual environment')).toBeInTheDocument();
    expect(within(details).getByText('Estimate confidence')).toBeInTheDocument();
    expect(within(details).getByText('Estimated')).toBeInTheDocument();
    expect(within(details).getByText('Measurement confidence')).toBeInTheDocument();
    expect(within(details).getByText('Measured')).toBeInTheDocument();
    expect(within(details).getAllByText('1.00 GB')).toHaveLength(2);
    expect(within(details).getAllByText('unknown').length).toBeGreaterThan(0);
  });

  it('ignores a disk measurement that finishes after engine data reloads', async () => {
    const response = makeEnginesResponse();
    response.tts.backends[0].disk_usage = {
      estimate: { model_download_bytes: 2 * 1024 ** 3, confidence: 'estimated' },
      actual: {},
    };
    let resolveMeasurement;
    const apiGetDiskUsage = vi.fn(() => new Promise((resolve) => (resolveMeasurement = resolve)));
    const apiListEngines = vi.fn().mockResolvedValue(response);
    const props = {
      family: 'tts',
      apiListEngines,
      apiGetEngineHealth: vi.fn(),
      apiGetDiskUsage,
    };
    const view = render(<EngineCompatibilityMatrix {...props} reloadToken={0} />);
    await waitForRow('omnivoice');
    const panel = openDetails('omnivoice');
    fireEvent.click(within(panel).getByRole('button', { name: 'Disk details' }));
    await waitFor(() => expect(apiGetDiskUsage).toHaveBeenCalledOnce());
    view.rerender(<EngineCompatibilityMatrix {...props} reloadToken={1} />);
    await waitFor(() => expect(apiListEngines).toHaveBeenCalledTimes(2));
    await act(async () => {
      resolveMeasurement({
        estimate: response.tts.backends[0].disk_usage.estimate,
        actual: { model_bytes: 9 * 1024 ** 3, confidence: 'measured' },
      });
    });
    expect(screen.queryByText('9.00 GB')).not.toBeInTheDocument();
  });

  it('names isolation in the detail panel only for subprocess engines', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('indextts2');
    expect(within(openDetails('indextts2')).getByText('Runs isolated')).toBeInTheDocument();
    expect(within(openDetails('omnivoice')).queryByText('Runs isolated')).not.toBeInTheDocument();
  });

  it('renders GPU compat chips for the selected engine in its detail panel', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    const omni = openDetails('omnivoice');
    expect(within(omni).getByText('CUDA')).toBeInTheDocument();
    expect(within(omni).getByText('MPS')).toBeInTheDocument();
    expect(within(omni).getByText('CPU')).toBeInTheDocument();
    const kitten = openDetails('kittentts');
    expect(within(kitten).getByText('CPU')).toBeInTheDocument();
    expect(within(kitten).queryByText('CUDA')).not.toBeInTheDocument();
  });

  it('shows the install reason in the detail panel when a backend is unavailable', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('kittentts');
    expect(within(rowOf('kittentts')).getByText('Needs setup')).toBeInTheDocument();
    expect(screen.queryByText('kittentts not installed')).not.toBeInTheDocument();
    const panel = openDetails('kittentts');
    expect(within(panel).getByText('kittentts not installed')).toBeInTheDocument();
    expect(within(panel).getByText('pip install kittentts')).toBeInTheDocument();
  });

  it('renders a "Last error" line in the open panel when last_error is populated', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('kittentts');
    openDetails('kittentts');
    const lastErrEls = screen.getAllByTestId('last-error');
    expect(lastErrEls.length).toBeGreaterThan(0);
    // The masked sentinel survives the redactor — the cache is rendered verbatim.
    expect(lastErrEls[0].textContent).toMatch(/hf_\*\*\*REDACTED\*\*\*/);
  });

  it('clicking Test engine fires getEngineHealth and renders latency_ms', async () => {
    const apiGetEngineHealth = vi
      .fn()
      .mockResolvedValue({ id: 'indextts2', ok: true, message: 'pong', latency_ms: 1234 });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={apiGetEngineHealth}
      />,
    );
    await waitForRow('indextts2');
    const panel = openDetails('indextts2');
    fireEvent.click(within(panel).getByRole('button', { name: /test indextts2/i }));
    await waitFor(() => expect(apiGetEngineHealth).toHaveBeenCalledWith('indextts2'));
    await waitFor(() =>
      expect(within(panel).getByTestId('health-result-indextts2')).toBeInTheDocument(),
    );
    expect(within(panel).getByText(/1234 ms/)).toBeInTheDocument();
  });

  it('Test button is disabled while an inflight health request is pending', async () => {
    let resolveHealth;
    const apiGetEngineHealth = vi.fn(() => new Promise((resolve) => (resolveHealth = resolve)));
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={apiGetEngineHealth}
      />,
    );
    await waitForRow('indextts2');
    const panel = openDetails('indextts2');
    const testBtn = within(panel).getByRole('button', { name: /test indextts2/i });
    fireEvent.click(testBtn);
    await waitFor(() => expect(testBtn).toBeDisabled());
    fireEvent.click(testBtn); // no-op while inflight
    expect(apiGetEngineHealth).toHaveBeenCalledTimes(1);
    resolveHealth({ id: 'indextts2', ok: true, message: 'pong', latency_ms: 50 });
  });

  // ── #21 routing display ────────────────────────────────────────────────
  function routingResponse() {
    const base = (over) => ({
      display_name: over.id,
      available: true,
      reason: null,
      install_hint: null,
      last_error: null,
      isolation_mode: 'in-process',
      ...over,
    });
    return {
      tts: {
        active: 'accel',
        backends: [
          base({
            id: 'accel',
            display_name: 'Accel TTS',
            gpu_compat: ['cuda', 'mps', 'cpu'],
            effective_device: 'cuda',
            routing_status: 'accelerated',
            routing_reason: null,
          }),
          base({
            id: 'fallback',
            display_name: 'Fallback TTS',
            gpu_compat: ['cuda', 'cpu'],
            effective_device: 'cpu',
            routing_status: 'cpu_fallback',
            routing_reason: 'engine has no CUDA path; running on CPU',
          }),
          base({
            id: 'gone',
            display_name: 'Unavail TTS',
            available: false,
            reason: 'needs cuda',
            gpu_compat: ['cuda'],
            effective_device: 'cuda',
            routing_status: 'unavailable',
            routing_reason: 'requires cuda; this host has cpu',
          }),
          // Legacy payload: no routing_* keys → render exactly as before.
          base({ id: 'legacy', display_name: 'Legacy TTS', gpu_compat: ['cpu'] }),
        ],
      },
      asr: { active: '', backends: [] },
      llm: {
        active: 'off',
        backends: [
          base({
            id: 'off',
            display_name: 'Off LLM',
            gpu_compat: [],
            effective_device: 'network',
            routing_status: 'n/a',
            routing_reason: null,
          }),
        ],
      },
    };
  }

  it('shows the effective device in the row and highlights its chip in the panel', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(routingResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('accel');
    const row = rowOf('accel');
    expect(within(row).getByTestId('runs-on-accel')).toHaveTextContent('CUDA');
    expect(within(row).getByText('GPU active')).toBeInTheDocument();
    const panel = openDetails('accel');
    expect(within(panel).getByText('CUDA').classList.contains('is-effective')).toBe(true);
    expect(within(panel).getByText('MPS').classList.contains('is-effective')).toBe(false);
  });

  it('uses the active locale for the Vulkan chip and effective-device tooltip', async () => {
    const localizedI18n = i18next.createInstance();
    await localizedI18n.init({
      lng: 'es',
      fallbackLng: false,
      resources: {
        es: {
          translation: {
            engines: {
              gpuVulkan: 'Vulkan localizada',
              routingEffectiveChip: 'Se ejecuta en {{device}}',
            },
          },
        },
      },
    });
    const response = routingResponse();
    response.tts.backends[0].gpu_compat = ['vulkan'];
    response.tts.backends[0].effective_device = 'vulkan';
    render(
      <I18nextProvider i18n={localizedI18n}>
        <EngineCompatibilityMatrix
          family="tts"
          apiListEngines={vi.fn().mockResolvedValue(response)}
          apiGetEngineHealth={vi.fn()}
        />
      </I18nextProvider>,
    );
    await waitForRow('accel');
    expect(within(rowOf('accel')).getByTestId('runs-on-accel')).toHaveTextContent(
      'Vulkan localizada',
    );
    const panel = openDetails('accel');
    expect(within(panel).getByText('Vulkan localizada')).toHaveAttribute(
      'title',
      'Se ejecuta en Vulkan localizada',
    );
  });

  it('reads "CPU fallback" in the status column for a cpu_fallback engine', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(routingResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('fallback');
    const row = rowOf('fallback');
    expect(within(row).getByText('CPU fallback')).toBeInTheDocument();
    expect(within(row).getByTestId('runs-on-fallback')).toHaveTextContent('CPU');
  });

  it('reads "Needs setup" for an unavailable engine, never a routing phrase', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(routingResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('gone');
    const row = rowOf('gone');
    expect(within(row).getByText('Needs setup')).toBeInTheDocument();
    expect(within(row).queryByText('GPU active')).not.toBeInTheDocument();
    expect(within(row).queryByText('CPU fallback')).not.toBeInTheDocument();
    // Runs-on names what it would need, muted.
    expect(within(row).getByTestId('runs-on-gone')).toHaveTextContent('CUDA');
  });

  it('renders a legacy (no-routing) payload as plainly Available', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(routingResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('legacy');
    const row = rowOf('legacy');
    expect(within(row).getByText('Available')).toBeInTheDocument();
    expect(within(row).queryByText('GPU active')).not.toBeInTheDocument();
  });

  it('shows "Remote" (not device chips) for LLM rows', async () => {
    render(
      <EngineCompatibilityMatrix
        family="llm"
        apiListEngines={vi.fn().mockResolvedValue(routingResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('off');
    expect(within(rowOf('off')).getByTestId('runs-on-off')).toHaveTextContent('Remote');
    const panel = openDetails('off');
    expect(within(panel).getByText('Remote')).toBeInTheDocument();
    expect(within(panel).queryByText('CPU')).not.toBeInTheDocument();
  });

  it('renders a failure marker when the health route returns ok=false', async () => {
    const apiGetEngineHealth = vi
      .fn()
      .mockResolvedValue({ id: 'indextts2', ok: false, message: 'spawn failed', latency_ms: 12 });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={apiGetEngineHealth}
      />,
    );
    await waitForRow('indextts2');
    const panel = openDetails('indextts2');
    fireEvent.click(within(panel).getByRole('button', { name: /test indextts2/i }));
    await waitFor(() =>
      expect(within(panel).getByTestId('health-result-indextts2')).toHaveTextContent(/failed/i),
    );
  });

  it('surfaces the routing reason as visible text in the panel, not only a hover title', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(routingResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('fallback');
    expect(within(rowOf('fallback')).getByTestId('runs-on-fallback')).toHaveAttribute(
      'title',
      'engine has no CUDA path; running on CPU',
    );
    const panel = openDetails('fallback');
    expect(within(panel).getByTestId('routing-reason-fallback')).toHaveTextContent(
      'engine has no CUDA path; running on CPU',
    );
    const accel = openDetails('accel');
    expect(within(accel).queryByTestId('routing-reason-accel')).not.toBeInTheDocument();
  });

  it('labels an in-process health check "deps OK" while subprocess shows real ms', async () => {
    const apiGetEngineHealth = vi
      .fn()
      .mockResolvedValue({ id: 'omnivoice', ok: true, message: 'import ok', latency_ms: 0 });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={apiGetEngineHealth}
      />,
    );
    await waitForRow('omnivoice');
    const panel = openDetails('omnivoice');
    fireEvent.click(within(panel).getByRole('button', { name: 'Test OmniVoice (test)' }));
    await waitFor(() =>
      expect(within(panel).getByTestId('health-result-omnivoice')).toHaveTextContent('deps OK'),
    );
    expect(within(panel).queryByText(/0 ms/)).not.toBeInTheDocument();
  });

  it('reflects the new active engine after select resolves, without a manual Refresh', async () => {
    let active = 'omnivoice';
    const resp = () => ({
      tts: {
        active,
        backends: [
          {
            id: 'omnivoice',
            display_name: 'OmniVoice (test)',
            available: true,
            reason: null,
            install_hint: null,
            last_error: null,
            isolation_mode: 'in-process',
            gpu_compat: ['cpu'],
          },
          {
            id: 'indextts2',
            display_name: 'IndexTTS2 (test)',
            available: true,
            reason: null,
            install_hint: null,
            last_error: null,
            isolation_mode: 'subprocess',
            gpu_compat: ['cuda', 'cpu'],
          },
        ],
      },
      asr: { active: '', backends: [] },
      llm: { active: 'off', backends: [] },
    });
    const apiListEngines = vi.fn(async () => resp());
    const onSelect = vi.fn(async (_family, id) => {
      active = id;
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        onSelect={onSelect}
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('indextts2');
    expect(within(rowOf('indextts2')).queryByText('active')).not.toBeInTheDocument();
    fireEvent.click(within(rowOf('indextts2')).getByRole('button', { name: /use indextts2/i }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('tts', 'indextts2'));
    await waitFor(() => expect(within(rowOf('indextts2')).getByText('active')).toBeInTheDocument());
    // The Use button leaves the now-active row; the old active row gains one.
    expect(
      within(rowOf('indextts2')).queryByRole('button', { name: /use indextts2/i }),
    ).not.toBeInTheDocument();
    expect(
      within(rowOf('omnivoice')).getByRole('button', { name: /use omnivoice/i }),
    ).toBeInTheDocument();
    expect(apiListEngines.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('mounts the Supertonic license dialog when "Accept license" is clicked', async () => {
    const apiListEngines = vi.fn().mockResolvedValue({
      tts: {
        active: 'omnivoice',
        backends: [
          {
            id: 'supertonic3',
            display_name: 'Supertonic-3',
            available: false,
            reason: 'Supertonic-3 license not accepted — review and accept to enable it.',
            install_hint: null,
            last_error: null,
            isolation_mode: 'in-process',
            gpu_compat: ['cpu'],
          },
        ],
      },
      asr: { active: '', backends: [] },
      llm: { active: 'off', backends: [] },
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('supertonic3');
    expect(screen.queryByText('Supertonic-3 — License Acceptance')).not.toBeInTheDocument();
    const panel = openDetails('supertonic3');
    fireEvent.click(
      within(panel).getByRole('button', { name: /review and accept supertonic-3 license/i }),
    );
    await waitFor(() =>
      expect(screen.getByText('Supertonic-3 — License Acceptance')).toBeInTheDocument(),
    );
  });

  it('mounts the PocketTTS terms dialog from an unavailable engine row', async () => {
    const apiListEngines = vi.fn().mockResolvedValue({
      tts: {
        active: 'omnivoice',
        backends: [
          {
            id: 'pockettts',
            display_name: 'PocketTTS',
            available: false,
            reason: 'PocketTTS license not accepted. Review the terms to enable it.',
            install_hint: null,
            last_error: null,
            isolation_mode: 'subprocess',
            gpu_compat: ['cpu'],
          },
        ],
      },
      asr: { active: '', backends: [] },
      llm: { active: 'off', backends: [] },
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('pockettts');
    const panel = openDetails('pockettts');
    fireEvent.click(
      within(panel).getByRole('button', { name: /review and accept pockettts license/i }),
    );
    await waitFor(() => {
      expect(screen.getByText('PocketTTS License Acceptance')).toBeInTheDocument();
      expect(screen.getByText('Review the access conditions')).toBeInTheDocument();
    });
  });

  // ── Real-synthesis self-test (in-process TTS engines) ──────────────────
  it('clicking Self-test runs a real synthesis and renders audio seconds + sample rate', async () => {
    const apiSelfTestEngine = vi.fn().mockResolvedValue({
      id: 'omnivoice',
      ok: true,
      message: 'synthesized',
      duration_ms: 820,
      sample_rate: 24000,
      num_samples: 19680,
      audio_seconds: 0.82,
      timed_out: false,
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiSelfTestEngine={apiSelfTestEngine}
      />,
    );
    await waitForRow('omnivoice');
    const panel = openDetails('omnivoice');
    fireEvent.click(within(panel).getByRole('button', { name: /self-test omnivoice/i }));
    await waitFor(() => expect(apiSelfTestEngine).toHaveBeenCalledWith('omnivoice'));
    await waitFor(() =>
      expect(within(panel).getByTestId('selftest-result-omnivoice')).toHaveTextContent(
        '0.82s @ 24 kHz in 820 ms',
      ),
    );
  });

  it('renders a timed-out marker when the self-test outruns the timeout', async () => {
    const apiSelfTestEngine = vi.fn().mockResolvedValue({
      id: 'omnivoice',
      ok: false,
      message: 'timed out after 90s (model still loading?)',
      duration_ms: 90000,
      timed_out: true,
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiSelfTestEngine={apiSelfTestEngine}
      />,
    );
    await waitForRow('omnivoice');
    const panel = openDetails('omnivoice');
    fireEvent.click(within(panel).getByRole('button', { name: /self-test omnivoice/i }));
    await waitFor(() =>
      expect(within(panel).getByTestId('selftest-result-omnivoice')).toHaveTextContent(
        'Self-test timed out',
      ),
    );
  });

  it('does not offer Self-test for a subprocess engine (spawn-and-ping only)', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiSelfTestEngine={vi.fn()}
      />,
    );
    await waitForRow('indextts2');
    const panel = openDetails('indextts2');
    expect(within(panel).getByRole('button', { name: /test indextts2/i })).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /self-test/i })).not.toBeInTheDocument();
  });

  it('does not offer Self-test on a non-TTS family (ASR)', async () => {
    const apiListEngines = vi.fn().mockResolvedValue({
      tts: { active: '', backends: [] },
      asr: {
        active: 'wx',
        backends: [
          {
            id: 'wx',
            display_name: 'WhisperX (test)',
            available: true,
            reason: null,
            install_hint: null,
            last_error: null,
            isolation_mode: 'in-process',
            gpu_compat: ['cpu'],
          },
        ],
      },
      llm: { active: 'off', backends: [] },
    });
    render(
      <EngineCompatibilityMatrix
        family="asr"
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
        apiSelfTestEngine={vi.fn()}
      />,
    );
    await waitForRow('wx');
    const panel = openDetails('wx');
    expect(within(panel).queryByRole('button', { name: /self-test/i })).not.toBeInTheDocument();
  });

  // ── Setup snippet for path-gated opt-in engines ────────────────────────
  it('renders the copy-paste setup snippet for a path-gated opt-in engine', async () => {
    const apiListEngines = vi.fn().mockResolvedValue({
      tts: {
        active: 'omnivoice',
        backends: [
          {
            id: 'indextts2',
            display_name: 'IndexTTS-2',
            available: false,
            reason: 'IndexTTS-2 venv not found. Set OMNIVOICE_INDEXTTS_DIR.',
            install_hint: 'git clone index-tts/index-tts',
            setup_snippet: 'export OMNIVOICE_INDEXTTS_DIR=/path/to/index-tts',
            last_error: null,
            isolation_mode: 'subprocess',
            gpu_compat: ['cuda', 'cpu'],
          },
        ],
      },
      asr: { active: '', backends: [] },
      llm: { active: 'off', backends: [] },
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
        apiSelfTestEngine={vi.fn()}
      />,
    );
    await waitForRow('indextts2');
    openDetails('indextts2');
    const snippet = screen.getByTestId('setup-snippet-indextts2');
    expect(snippet).toHaveTextContent('export OMNIVOICE_INDEXTTS_DIR=/path/to/index-tts');
    expect(
      within(snippet).getByRole('button', { name: /copy setup command/i }),
    ).toBeInTheDocument();
  });

  it('shows no setup snippet for a bundled engine', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiSelfTestEngine={vi.fn()}
      />,
    );
    await waitForRow('kittentts');
    openDetails('kittentts');
    expect(screen.queryByTestId('setup-snippet-kittentts')).not.toBeInTheDocument();
  });

  // ── #981 — mlx-audio curated-model picker ───────────────────────────────
  function mlxAudioResponse({ activeModelId = 'kokoro' } = {}) {
    return {
      tts: {
        active: 'mlx-audio',
        backends: [
          {
            id: 'mlx-audio',
            display_name: 'MLX-Audio (test)',
            available: true,
            reason: null,
            install_hint: null,
            last_error: null,
            isolation_mode: 'in-process',
            gpu_compat: ['mps', 'cpu'],
            curated_models: [
              {
                key: 'kokoro',
                label: 'Kokoro (default, fast)',
                repo_id: 'mlx-community/Kokoro-82M-bf16',
              },
              { key: 'csm', label: 'CSM (voice cloning)', repo_id: 'mlx-community/csm-1b-8bit' },
              {
                key: 'outetts',
                label: 'OuteTTS',
                repo_id: 'mlx-community/Llama-OuteTTS-1.0-1B-4bit',
              },
            ],
            active_model_id: activeModelId,
          },
        ],
      },
      asr: { active: '', backends: [] },
      llm: { active: 'off', backends: [] },
    };
  }

  it('renders the curated-model picker for mlx-audio, pre-selected to the active model', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        onSelect={vi.fn()}
        apiListEngines={vi.fn().mockResolvedValue(mlxAudioResponse({ activeModelId: 'outetts' }))}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('mlx-audio');
    openDetails('mlx-audio');
    const select = screen.getByTestId('curated-model-select-mlx-audio');
    expect(select).toHaveValue('outetts');
    expect(
      within(select).getByRole('option', { name: 'Kokoro (default, fast)' }),
    ).toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'CSM (voice cloning)' })).toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'OuteTTS' })).toBeInTheDocument();
  });

  it('picking a different curated model calls onSelect with the model key and refreshes', async () => {
    let activeModelId = 'kokoro';
    const apiListEngines = vi.fn(async () => mlxAudioResponse({ activeModelId }));
    const onSelect = vi.fn(async (_family, _id, modelId) => {
      activeModelId = modelId;
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        onSelect={onSelect}
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('mlx-audio');
    openDetails('mlx-audio');
    fireEvent.change(screen.getByTestId('curated-model-select-mlx-audio'), {
      target: { value: 'csm' },
    });
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('tts', 'mlx-audio', 'csm'));
    // Reloaded after the pick — the panel stays open and reflects the new model.
    await waitFor(() =>
      expect(screen.getByTestId('curated-model-select-mlx-audio')).toHaveValue('csm'),
    );
    expect(apiListEngines.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('does not render a curated-model picker for engines without curated_models', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        onSelect={vi.fn()}
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    openDetails('omnivoice');
    expect(screen.queryByTestId(/curated-model-select-/)).not.toBeInTheDocument();
  });

  it('disables the curated-model picker when no onSelect is provided', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(mlxAudioResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('mlx-audio');
    openDetails('mlx-audio');
    expect(screen.getByTestId('curated-model-select-mlx-audio')).toBeDisabled();
  });

  // ── Family modes ────────────────────────────────────────────────────────
  function multiFamilyResponse() {
    return {
      tts: {
        active: 'omnivoice',
        backends: [
          {
            id: 'omnivoice',
            display_name: 'OmniVoice (test)',
            available: true,
            reason: null,
            install_hint: null,
            last_error: null,
            isolation_mode: 'in-process',
            gpu_compat: ['cpu'],
          },
        ],
      },
      asr: {
        active: 'whisperx',
        backends: [
          {
            id: 'whisperx',
            display_name: 'WhisperX (test)',
            available: true,
            reason: null,
            install_hint: null,
            last_error: null,
            isolation_mode: 'in-process',
            gpu_compat: ['cpu'],
          },
        ],
      },
      llm: { active: 'off', backends: [] },
    };
  }

  it('pins to the given family and hides the TTS/ASR/LLM switcher when showFamilyTabs is false', async () => {
    render(
      <EngineCompatibilityMatrix
        family="asr"
        showFamilyTabs={false}
        apiListEngines={vi.fn().mockResolvedValue(multiFamilyResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('whisperx');
    expect(screen.getByText('ASR Engines')).toBeInTheDocument();
    expect(screen.queryByText('OmniVoice (test)')).not.toBeInTheDocument();
    expect(document.querySelector('.engine-matrix__tab-family')).toBeNull();
  });

  it('keeps the family switcher by default (standalone mounts unchanged)', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(multiFamilyResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    expect(screen.getByRole('heading', { name: 'Engines' })).toBeInTheDocument();
    expect(document.querySelectorAll('.engine-matrix__tab-family').length).toBe(3);
    for (const label of document.querySelectorAll('.engine-matrix__tab-label')) {
      expect(label).toHaveClass('items-center');
    }
  });

  it('names what each family does in pinned mode (one description line)', async () => {
    render(
      <EngineCompatibilityMatrix
        family="asr"
        showFamilyTabs={false}
        apiListEngines={vi.fn().mockResolvedValue(multiFamilyResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('whisperx');
    expect(screen.getByTestId('family-desc-asr')).toHaveTextContent(/turns audio into text/i);
  });

  it('forgets the selected row when the family changes', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(multiFamilyResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    openDetails('omnivoice');
    expect(screen.getByTestId('engine-detail-omnivoice')).toBeInTheDocument();
    const asrTab = screen.getByRole('tab', { name: /ASR/ });
    fireEvent.pointerDown(asrTab, { button: 0, pointerType: 'mouse' });
    fireEvent.mouseDown(asrTab, { button: 0 });
    fireEvent.click(asrTab);
    await waitForRow('whisperx');
    expect(screen.queryByTestId('engine-detail-omnivoice')).not.toBeInTheDocument();
    expect(screen.getByTestId('engine-detail-empty')).toBeInTheDocument();
  });

  // ── Engine identity mark ────────────────────────────────────────────────
  it('renders a deterministic identity mark on every engine row', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    expect(screen.getByTestId('engine-mark-omnivoice')).toHaveTextContent('OM');
    expect(screen.getByTestId('engine-mark-indextts2')).toHaveTextContent('IN');
    expect(screen.getByTestId('engine-mark-kittentts')).toBeInTheDocument();
    expect(screen.getByTestId('engine-mark-omnivoice')).toHaveAttribute('aria-hidden', 'true');
  });

  // ── `hint` — available-but-has-advice rows ──────────────────────────────
  function hintResponse() {
    const resp = makeEnginesResponse();
    resp.tts.backends[0].hint =
      'installed voxcpm 2.0.1 is older than 2.0.3 — upgrading is recommended';
    return resp;
  }

  it('renders the ok-with-advice hint in the panel of an available engine', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(hintResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    // Quiet by design: the row stays one line, the advice waits in the panel.
    expect(screen.queryByTestId('engine-hint-omnivoice')).not.toBeInTheDocument();
    const panel = openDetails('omnivoice');
    expect(within(panel).getByTestId('engine-hint-omnivoice')).toHaveTextContent(
      'installed voxcpm 2.0.1 is older than 2.0.3 — upgrading is recommended',
    );
    expect(within(openDetails('indextts2')).queryByTestId('engine-hint-indextts2')).toBeNull();
  });

  // ── Capability badge — voice cloning ────────────────────────────────────
  function cloningResponse() {
    const resp = makeEnginesResponse();
    resp.tts.backends[0].supports_cloning = true; // omnivoice
    resp.tts.backends[2].supports_cloning = false; // indextts2 — explicit false
    return resp;
  }

  it('badges voice-cloning-capable engines — and only on explicit true', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(cloningResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    expect(screen.getByTestId('clone-badge-omnivoice')).toHaveTextContent('Voice cloning');
    expect(screen.queryByTestId('clone-badge-indextts2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('clone-badge-kittentts')).not.toBeInTheDocument();
  });

  it('never badges cloning on a non-TTS family (capability is TTS-only)', async () => {
    const resp = multiFamilyResponse();
    resp.asr.backends[0].supports_cloning = true; // hostile/buggy payload
    render(
      <EngineCompatibilityMatrix
        family="asr"
        showFamilyTabs={false}
        apiListEngines={vi.fn().mockResolvedValue(resp)}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('whisperx');
    expect(screen.queryByTestId('clone-badge-whisperx')).not.toBeInTheDocument();
  });

  // ── Memory residency — "In memory" chip + Unload ────────────────────────
  const LOADED = {
    models: [
      {
        id: 'sidecar:indextts2',
        name: 'indextts2 (sidecar)',
        checkpoint: 'indextts2',
        device: 'mps',
        vram_mb: 812.5,
        unloadable: true,
        engine_id: 'indextts2',
        is_active_engine: false,
      },
    ],
    count: 1,
  };

  it('marks a loaded engine "In memory" and unloads it via its /model/loaded id', async () => {
    let loaded = LOADED;
    const apiListLoadedModels = vi.fn(async () => loaded);
    const apiUnloadModel = vi.fn(async () => {
      loaded = { models: [], count: 0 };
      return { unloaded: 'sidecar:indextts2', success: true };
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiListLoadedModels={apiListLoadedModels}
        apiUnloadModel={apiUnloadModel}
      />,
    );
    await waitForRow('indextts2');
    await waitFor(() =>
      expect(within(rowOf('indextts2')).getByTestId('resident-indextts2')).toHaveTextContent(
        'In memory',
      ),
    );
    expect(within(rowOf('omnivoice')).queryByTestId('resident-omnivoice')).not.toBeInTheDocument();
    const panel = openDetails('indextts2');
    fireEvent.click(within(panel).getByRole('button', { name: /unload indextts2/i }));
    await waitFor(() => expect(apiUnloadModel).toHaveBeenCalledWith('sidecar:indextts2'));
    await waitFor(() =>
      expect(
        within(rowOf('indextts2')).queryByTestId('resident-indextts2'),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(panel).queryByRole('button', { name: /unload indextts2/i }),
    ).not.toBeInTheDocument();
  });

  it('offers no Unload when the loaded entry is not unloadable', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiListLoadedModels={vi.fn().mockResolvedValue({
          models: [{ ...LOADED.models[0], unloadable: false }],
          count: 1,
        })}
        apiUnloadModel={vi.fn()}
      />,
    );
    await waitForRow('indextts2');
    await waitFor(() =>
      expect(within(rowOf('indextts2')).getByTestId('resident-indextts2')).toBeInTheDocument(),
    );
    const panel = openDetails('indextts2');
    expect(
      within(panel).queryByRole('button', { name: /unload indextts2/i }),
    ).not.toBeInTheDocument();
  });

  it('renders the list normally when the residency probe fails (advisory only)', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
        apiListLoadedModels={vi.fn().mockRejectedValue(new Error('backend restarting'))}
      />,
    );
    await waitForRow('omnivoice');
    expect(document.querySelectorAll('[data-engine-id]').length).toBe(3);
    expect(screen.queryByTestId('resident-indextts2')).not.toBeInTheDocument();
  });

  // ── Selection + detail panel ────────────────────────────────────────────
  it('selects a row from its name, opens its panel beside the list, and toggles it closed', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('kittentts');
    const toggle = nameOf('kittentts');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('engine-detail-kittentts')).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const panel = screen.getByTestId('engine-detail-kittentts');
    expect(toggle).toHaveAttribute('aria-controls', panel.id);
    expect(rowOf('kittentts')).toHaveAttribute('aria-selected', 'true');
    // The panel is outside the table (a sibling column), never inside a row.
    expect(screen.getByRole('table').contains(panel)).toBe(false);
    expect(within(panel).getByText('kittentts not installed')).toBeInTheDocument();

    // Selecting another row swaps the panel; clicking the name again closes it.
    fireEvent.click(rowOf('omnivoice'));
    expect(screen.queryByTestId('engine-detail-kittentts')).not.toBeInTheDocument();
    expect(screen.getByTestId('engine-detail-omnivoice')).toBeInTheDocument();
    fireEvent.click(nameOf('omnivoice'));
    expect(screen.queryByTestId('engine-detail-omnivoice')).not.toBeInTheDocument();
    expect(screen.getByTestId('engine-detail-empty')).toBeInTheDocument();
  });

  it('every row offers details — available engines too', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeEnginesResponse())}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('omnivoice');
    for (const id of ['omnivoice', 'indextts2', 'kittentts']) {
      expect(nameOf(id)).toHaveAttribute('aria-expanded', 'false');
    }
  });

  // ── One-click sidecar install (IndexTTS-2 & friends) ────────────────────
  function makeInstallableResponse() {
    const res = makeEnginesResponse();
    res.tts.backends[2] = {
      ...res.tts.backends[2],
      available: false,
      reason: 'IndexTTS-2 venv not found.',
      setup_snippet: 'export OMNIVOICE_INDEXTTS_DIR=/path/to/index-tts',
      one_click_install: true,
    };
    return res;
  }

  function makeIdleStatus() {
    return {
      engine_id: 'indextts2',
      installed: false,
      managed: false,
      install_dir: null,
      job: null,
    };
  }

  function makeInstallStatus(jobState, overrides = {}) {
    return {
      engine_id: 'indextts2',
      installed: false,
      managed: false,
      install_dir: null,
      job: {
        engine_id: 'indextts2',
        state: jobState,
        steps: [
          { id: 'preflight', state: 'done', detail: null },
          { id: 'fetch_source', state: 'running', detail: null },
          { id: 'create_venv', state: 'pending', detail: null },
          { id: 'install_deps', state: 'pending', detail: null },
          { id: 'verify', state: 'pending', detail: null },
          { id: 'fetch_weights', state: 'pending', detail: null },
          { id: 'persist', state: 'pending', detail: null },
        ],
        log: ['Cloning https://github.com/index-tts/index-tts.git (git, depth 1) …'],
        error: null,
        remediation: null,
        weights_progress: null,
        started_at: 1,
        finished_at: null,
        ...overrides,
      },
    };
  }

  it('installable unavailable rows get an Install button; clicking it starts the job and shows step progress', async () => {
    const apiInstallEngine = vi.fn().mockResolvedValue({ status: 'started', engine: 'indextts2' });
    const apiInstallStatus = vi
      .fn()
      .mockResolvedValueOnce(makeIdleStatus())
      .mockResolvedValue(makeInstallStatus('running'));
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallEngine={apiInstallEngine}
        apiInstallStatus={apiInstallStatus}
      />,
    );
    await waitForRow('indextts2');
    const installBtn = screen.getByTestId('install-indextts2');
    expect(installBtn).toHaveTextContent('Install');
    fireEvent.click(installBtn);
    await waitFor(() => expect(apiInstallEngine).toHaveBeenCalledWith('indextts2'));
    expect(apiInstallStatus).toHaveBeenCalledWith('indextts2');
    // Clicking selects the row so the progress renders in its panel.
    const progress = await screen.findByTestId('install-progress-indextts2');
    expect(screen.getByTestId('engine-detail-indextts2').contains(progress)).toBe(true);
    expect(within(progress).getByText(/Checking uv and disk space/)).toBeInTheDocument();
    expect(progress.querySelector('[data-install-step="fetch_source"]')).toHaveAttribute(
      'data-step-state',
      'running',
    );
    expect(within(progress).getByText(/Cloning https:\/\//)).toBeInTheDocument();
    expect(screen.getByTestId('install-indextts2')).toHaveTextContent('Installing…');
    expect(
      within(rowOf('indextts2')).getByText('Installing…', { selector: 'td' }),
    ).toBeInTheDocument();
  });

  it('a failed job renders the error with its remediation and offers Retry', async () => {
    const apiInstallEngine = vi.fn().mockResolvedValue({ status: 'started', engine: 'indextts2' });
    const apiInstallStatus = vi
      .fn()
      .mockResolvedValueOnce(makeIdleStatus())
      .mockResolvedValue(
        makeInstallStatus('failed', {
          error: 'Not enough disk space to install IndexTTS-2',
          remediation: 'Free up disk space and retry.',
          finished_at: 2,
        }),
      );
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallEngine={apiInstallEngine}
        apiInstallStatus={apiInstallStatus}
      />,
    );
    await waitForRow('indextts2');
    fireEvent.click(screen.getByTestId('install-indextts2'));
    const progress = await screen.findByTestId('install-progress-indextts2');
    expect(
      within(progress).getByText(/Not enough disk space .* Free up disk space and retry\./),
    ).toBeInTheDocument();
    expect(screen.getByTestId('install-indextts2')).toHaveTextContent('Retry install');
    expect(within(rowOf('indextts2')).getByText('failed', { selector: 'td' })).toBeInTheDocument();
  });

  it('an Install click during the mount status probe is not silently dropped', async () => {
    const apiInstallEngine = vi.fn().mockResolvedValue({ status: 'started', engine: 'indextts2' });
    let releaseProbe;
    const probeGate = new Promise((resolve) => {
      releaseProbe = resolve;
    });
    const apiInstallStatus = vi
      .fn()
      .mockImplementationOnce(async () => {
        await probeGate;
        return makeIdleStatus();
      })
      .mockResolvedValue(makeInstallStatus('running'));
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallEngine={apiInstallEngine}
        apiInstallStatus={apiInstallStatus}
      />,
    );
    await waitForRow('indextts2');
    fireEvent.click(screen.getByTestId('install-indextts2'));
    releaseProbe();
    expect(
      await screen.findByTestId('install-progress-indextts2', {}, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it('two rapid Install clicks never fetch status concurrently', async () => {
    const apiInstallEngine = vi.fn().mockResolvedValue({ status: 'started', engine: 'indextts2' });
    let releaseProbe;
    const probeGate = new Promise((resolve) => {
      releaseProbe = resolve;
    });
    let active = 0;
    let maxActive = 0;
    const apiInstallStatus = vi
      .fn()
      .mockImplementationOnce(async () => {
        await probeGate;
        return makeIdleStatus();
      })
      .mockImplementation(async () => {
        active += 1;
        maxActive = Math.max(maxActive, active);
        await new Promise((resolve) => setTimeout(resolve, 20));
        active -= 1;
        return makeInstallStatus('running');
      });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallEngine={apiInstallEngine}
        apiInstallStatus={apiInstallStatus}
      />,
    );
    await waitForRow('indextts2');
    fireEvent.click(screen.getByTestId('install-indextts2'));
    fireEvent.click(screen.getByTestId('install-indextts2'));
    releaseProbe();
    await screen.findByTestId('install-progress-indextts2', {}, { timeout: 3000 });
    expect(maxActive).toBe(1);
  });

  it('a wedged probe cannot stall the Install click forever, nor clobber it late', async () => {
    const apiInstallEngine = vi.fn().mockResolvedValue({ status: 'started', engine: 'indextts2' });
    let releaseProbe;
    const probeGate = new Promise((resolve) => {
      releaseProbe = resolve;
    });
    const apiInstallStatus = vi
      .fn()
      .mockImplementationOnce(async () => {
        await probeGate;
        return makeIdleStatus(); // stale: pre-install idle, arriving very late
      })
      .mockResolvedValue(makeInstallStatus('running'));
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallEngine={apiInstallEngine}
        apiInstallStatus={apiInstallStatus}
      />,
    );
    await waitForRow('indextts2');
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByTestId('install-indextts2'));
      await vi.advanceTimersByTimeAsync(FORCE_WAIT_TIMEOUT_MS + 50);
    } finally {
      vi.useRealTimers();
    }
    await screen.findByTestId('install-progress-indextts2', {}, { timeout: 3000 });
    releaseProbe();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.getByTestId('install-progress-indextts2')).toBeInTheDocument();
  });

  it('already_installed responses skip the job and just reload the list', async () => {
    const apiListEngines = vi.fn().mockResolvedValue(makeInstallableResponse());
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={apiListEngines}
        apiGetEngineHealth={vi.fn()}
        apiInstallEngine={vi
          .fn()
          .mockResolvedValue({ status: 'already_installed', engine: 'indextts2' })}
        apiInstallStatus={vi.fn().mockResolvedValue(makeIdleStatus())}
      />,
    );
    await waitForRow('indextts2');
    fireEvent.click(screen.getByTestId('install-indextts2'));
    await waitFor(() => expect(apiListEngines).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('install-progress-indextts2')).not.toBeInTheDocument();
  });

  it('demotes the manual setup snippet to a collapsed fallback on installable rows', async () => {
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallStatus={vi.fn().mockResolvedValue(makeIdleStatus())}
      />,
    );
    await waitForRow('indextts2');
    openDetails('indextts2');
    const manual = screen.getByTestId('manual-install-indextts2');
    expect(manual.tagName).toBe('DETAILS');
    expect(manual).not.toHaveAttribute('open');
    expect(within(manual).getByTestId('setup-snippet-indextts2')).toBeInTheDocument();
  });

  it('re-attaches to an in-flight install job on mount (no click needed)', async () => {
    const apiInstallStatus = vi.fn().mockResolvedValue(makeInstallStatus('running'));
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(makeInstallableResponse())}
        apiGetEngineHealth={vi.fn()}
        apiInstallStatus={apiInstallStatus}
      />,
    );
    await waitForRow('indextts2');
    await waitFor(() =>
      expect(screen.getByTestId('install-indextts2')).toHaveTextContent('Installing…'),
    );
    expect(apiInstallStatus).toHaveBeenCalledWith('indextts2');
  });

  it('keeps the setup snippet top-level on rows without a one-click installer', async () => {
    const res = makeEnginesResponse();
    res.tts.backends[1].setup_snippet = 'export OMNIVOICE_SHERPA_MODEL=/m';
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(res)}
        apiGetEngineHealth={vi.fn()}
      />,
    );
    await waitForRow('kittentts');
    openDetails('kittentts');
    expect(screen.getByTestId('setup-snippet-kittentts')).toBeInTheDocument();
    expect(screen.queryByTestId('manual-install-kittentts')).not.toBeInTheDocument();
    expect(screen.queryByTestId('install-kittentts')).not.toBeInTheDocument();
  });

  it('opens the LLM Providers panel from the openai-compat row and names the provider', async () => {
    const { useAppStore } = await import('../store');
    const response = routingResponse();
    response.llm.backends.push({
      id: 'openai-compat',
      display_name: 'OpenAI-compatible',
      available: true,
      reason: null,
      install_hint: null,
      last_error: null,
      isolation_mode: 'in-process',
      gpu_compat: [],
      effective_device: 'network',
      routing_status: 'n/a',
      routing_reason: null,
      hint: 'OrcaRouter · gpt-4o-mini',
    });
    const original = useAppStore.getState().openSettingsTab;
    const openSettingsTab = vi.fn();
    useAppStore.setState({ openSettingsTab });
    try {
      render(
        <EngineCompatibilityMatrix
          family="llm"
          apiListEngines={vi.fn().mockResolvedValue(response)}
          apiGetEngineHealth={vi.fn()}
        />,
      );
      await waitForRow('openai-compat');
      const panel = openDetails('openai-compat');
      expect(within(panel).getByText('OrcaRouter · gpt-4o-mini')).toBeInTheDocument();
      fireEvent.click(within(panel).getByTestId('configure-llm-providers'));
      expect(openSettingsTab).toHaveBeenCalledWith('llm-providers');
    } finally {
      useAppStore.setState({ openSettingsTab: original });
    }
  });
});

describe('EngineCompatibilityMatrix one-click install', () => {
  function renderWithRow(reason) {
    const res = makeEnginesResponse();
    res.tts.backends.push({
      id: 'pockettts',
      display_name: 'PocketTTS (test)',
      available: false,
      reason,
      one_click_install: true,
      install_hint: '',
      last_error: null,
      isolation_mode: 'subprocess',
      gpu_compat: ['cpu'],
    });
    render(
      <EngineCompatibilityMatrix
        family="tts"
        apiListEngines={vi.fn().mockResolvedValue(res)}
        apiGetEngineHealth={vi.fn()}
        apiInstallStatus={vi.fn().mockResolvedValue({ state: 'idle' })}
      />,
    );
    return waitFor(() => screen.getByText('PocketTTS (test)'));
  }

  it('offers Install for an engine that is not installed yet', async () => {
    await renderWithRow(
      "This engine's package isn't installed yet. Install it from Model Catalogue.",
    );
    expect(screen.getByTestId('install-pockettts')).toBeInTheDocument();
  });

  it('offers only the license review once the engine is installed', async () => {
    await renderWithRow(
      'License not accepted yet. Review and accept it in Model Catalogue to enable this engine.',
    );
    expect(screen.queryByTestId('install-pockettts')).not.toBeInTheDocument();
  });
});
