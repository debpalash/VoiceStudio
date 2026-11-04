import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BackendStatus } from '@/hooks/use-backend-status';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  bridge: null as unknown,
  activities: { synthesis: 0 },
  status: {
    stage: 'ready',
    managed: true,
    remote: false,
    baseUrl: 'http://127.0.0.1:3900',
    port: 3900,
    elapsedMs: 0,
    logTail: [],
  } as BackendStatus,
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/app-activity', () => ({ useAppActivities: () => mock.activities }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => mock.status }));
vi.mock('@/components/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/bridge')>()),
  getBridge: () => mock.bridge,
}));
vi.mock('./system-preflight', () => ({ SystemPreflight: () => null }));
vi.mock('@/components/performance-profile', () => ({ PerformanceProfile: () => null }));
vi.mock('@shared/components/SearchableSelect', async (importOriginal) => {
  const original = await importOriginal<{
    default: React.ComponentType<Record<string, unknown>>;
  }>();
  return {
    default: (props: {
      ariaLabel: string;
      onChange: (value: string) => void;
      disabled: boolean;
    }) =>
      props.ariaLabel === 'runtimeTorch.title' ? (
        <original.default {...props} />
      ) : (
        <button disabled={props.disabled} onClick={() => props.onChange('GPU-second')}>
          Choose adapter
        </button>
      ),
  };
});
import { PerformanceSettings } from './performance-settings';

beforeEach(() => {
  mock.bridge = null;
  mock.activities.synthesis = 0;
  mock.status.stage = 'ready';
  mock.status.remote = false;
  mock.status.managed = true;
  delete mock.status.runtimeTorchPreference;
  delete mock.status.runtimeTorchSupported;
  delete mock.status.runtimeTorchSwitchable;
  delete mock.status.runtimeTorchPinned;
  delete mock.status.runtimeTorchSetupPending;
  Element.prototype.scrollIntoView = vi.fn();
  mock.api.mockImplementation(async (path: string) => {
    if (path === '/api/settings/cuda-device') return { value: 'auto', devices: [] };
    if (path === '/api/settings/compute-device')
      return {
        value: 'cpu',
        effective_family: 'cpu',
        available_families: ['cpu'],
        env_pinned: true,
      };
    if (path === '/model/loaded') return { models: [], count: 0 };
    return {};
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPerformance = () =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <PerformanceSettings />
    </QueryClientProvider>,
  );

const openRuntimeConfirmation = async () => {
  const entry = screen.getByRole('button', { name: 'runtimeTorch.change' });
  entry.focus();
  fireEvent.click(entry);
  return screen.findByRole('dialog', { name: 'runtimeTorch.change' });
};

const chooseRuntime = async (value = 'rocm') => {
  fireEvent.click(screen.getByRole('button', { name: 'runtimeTorch.title' }));
  const search = await screen.findByRole('textbox', { name: 'common.search' });
  fireEvent.change(search, { target: { value: `runtimeTorch.${value}` } });
  fireEvent.keyDown(search, { key: 'ArrowDown' });
  fireEvent.keyDown(search, { key: 'Enter' });
};

it('keeps browser and unsupported settings free of runtime migration controls', () => {
  renderPerformance();
  expect(screen.queryByRole('button', { name: 'runtimeTorch.change' })).not.toBeInTheDocument();
});

it.each(['remote', 'unowned', 'busy'])('hides migration controls for a %s backend', (kind) => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.remote = kind === 'remote';
  mock.status.managed = kind !== 'unowned';
  if (kind === 'busy') mock.status.stage = 'unresponsive';
  renderPerformance();
  expect(screen.queryByRole('button', { name: 'runtimeTorch.change' })).not.toBeInTheDocument();
});

it('closes only the package menu on Escape and restores the selector focus', async () => {
  mock.status.runtimeTorchSwitchable = true;
  renderPerformance();
  await openRuntimeConfirmation();
  const trigger = screen.getByRole('button', { name: 'runtimeTorch.title' });
  fireEvent.click(trigger);
  const search = await screen.findByRole('textbox', { name: 'common.search' });
  await waitFor(() => expect(search).toHaveFocus());
  fireEvent.keyDown(search, { key: 'Escape' });
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: 'runtimeTorch.change' })).toBeVisible();
  expect(trigger).toHaveFocus();
});

it('keeps the confirmation open when choosing a portaled option with the mouse', async () => {
  mock.status.runtimeTorchSwitchable = true;
  const begin = vi.fn();
  mock.bridge = { backend: { beginRuntimeTorchSetup: begin } };
  renderPerformance();
  await openRuntimeConfirmation();
  const trigger = screen.getByRole('button', { name: 'runtimeTorch.title' });
  fireEvent.click(trigger);
  fireEvent.mouseDown(screen.getByRole('option', { name: 'runtimeTorch.rocm' }));
  expect(trigger).toHaveTextContent('runtimeTorch.rocm');
  expect(screen.getByRole('dialog', { name: 'runtimeTorch.change' })).toBeVisible();
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(begin).not.toHaveBeenCalled();
});

it.each(['button', 'escape'])(
  'cancels the local runtime choice through %s without side effects',
  async (method) => {
    mock.status.runtimeTorchSwitchable = true;
    mock.status.runtimeTorchPreference = 'cpu';
    const begin = vi.fn();
    const save = vi.fn();
    mock.bridge = { backend: { beginRuntimeTorchSetup: begin, setRuntimeTorchPreference: save } };
    renderPerformance();
    const dialog = await openRuntimeConfirmation();
    expect(dialog).toHaveAccessibleDescription(
      'runtimeTorch.confirmDescription runtimeTorch.packagesEffect',
    );
    expect(within(dialog).getByRole('button', { name: 'runtimeTorch.confirm' })).toBeDisabled();
    await chooseRuntime();
    expect(begin).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
    if (method === 'button')
      fireEvent.click(within(dialog).getByRole('button', { name: 'common.cancel' }));
    else fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'runtimeTorch.change' })).toHaveFocus(),
    );
    await openRuntimeConfirmation();
    expect(screen.getByRole('button', { name: 'runtimeTorch.title' })).toHaveTextContent(
      'runtimeTorch.cpu',
    );
    expect(begin).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
  },
);

it('requires explicit confirmation and blocks duplicate requests without installing or changing CPU pins', async () => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.runtimeTorchPreference = 'cpu';
  let completeBegin!: () => void;
  const begin = vi.fn().mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        completeBegin = resolve;
      }),
  );
  const setupRuntime = vi.fn();
  const cleanSetupRuntime = vi.fn();
  const save = vi.fn();
  mock.bridge = {
    backend: {
      beginRuntimeTorchSetup: begin,
      setupRuntime,
      cleanSetupRuntime,
      setRuntimeTorchPreference: save,
    },
  };
  renderPerformance();
  const dialog = await openRuntimeConfirmation();
  await chooseRuntime();
  expect(begin).not.toHaveBeenCalled();
  const confirm = within(dialog).getByRole('button', { name: 'runtimeTorch.confirm' });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(begin).toHaveBeenCalledExactlyOnceWith('rocm');
  expect(confirm).toBeDisabled();
  expect(screen.getByRole('button', { name: 'runtimeTorch.title' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'common.cancel' })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('runtimeTorch.preparing');
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(setupRuntime).not.toHaveBeenCalled();
  expect(cleanSetupRuntime).not.toHaveBeenCalled();
  expect(save).not.toHaveBeenCalled();
  expect(mock.api.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0);
  completeBegin();
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(setupRuntime).not.toHaveBeenCalled();
});

it('announces a failed migration request and allows retry or cancellation without installation', async () => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.runtimeTorchPreference = 'cpu';
  const begin = vi.fn().mockRejectedValue(new Error('private supervisor refusal'));
  const setupRuntime = vi.fn();
  mock.bridge = { backend: { beginRuntimeTorchSetup: begin, setupRuntime } };
  renderPerformance();
  const dialog = await openRuntimeConfirmation();
  await chooseRuntime('cuda');
  fireEvent.click(within(dialog).getByRole('button', { name: 'runtimeTorch.confirm' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('runtimeTorch.beginError');
  expect(screen.queryByText('private supervisor refusal')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'runtimeTorch.title' })).toHaveTextContent(
    'runtimeTorch.cuda',
  );
  expect(screen.getByRole('button', { name: 'runtimeTorch.confirm' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'runtimeTorch.confirm' }));
  await waitFor(() => expect(begin).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole('button', { name: 'common.cancel' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(setupRuntime).not.toHaveBeenCalled();
});

it.each([
  ['rocm', 'cpu'],
  ['cpu', 'cuda'],
  ['cpu', 'auto'],
  ['cpu', 'rocm'],
] as const)('confirms %s → %s only through the explicit setup entry', async (previous, next) => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.runtimeTorchPreference = previous;
  const begin = vi.fn().mockResolvedValue(undefined);
  const install = vi.fn();
  const save = vi.fn();
  mock.bridge = {
    backend: {
      beginRuntimeTorchSetup: begin,
      setupRuntime: install,
      setRuntimeTorchPreference: save,
    },
  };
  renderPerformance();
  await openRuntimeConfirmation();
  await chooseRuntime(next);
  expect(begin).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'runtimeTorch.confirm' }));
  await waitFor(() => expect(begin).toHaveBeenCalledExactlyOnceWith(next));
  expect(install).not.toHaveBeenCalled();
  expect(save).not.toHaveBeenCalled();
});

it('disables the runtime choice entry for an environment pin', () => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.runtimeTorchPinned = true;
  const begin = vi.fn();
  mock.bridge = { backend: { beginRuntimeTorchSetup: begin } };
  renderPerformance();
  const entry = screen.getByRole('button', { name: 'runtimeTorch.change' });
  expect(entry).toBeDisabled();
  fireEvent.click(entry);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(begin).not.toHaveBeenCalled();
  expect(screen.getByText('runtimeTorch.pinned')).toBeVisible();
});

it('allows a next-start choice while current work continues', async () => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.runtimeTorchPreference = 'cpu';
  const begin = vi.fn().mockResolvedValue(undefined);
  const restart = vi.fn();
  const setupRuntime = vi.fn();
  mock.bridge = { backend: { beginRuntimeTorchSetup: begin, restart, setupRuntime } };
  const view = renderPerformance();
  await openRuntimeConfirmation();
  await chooseRuntime();
  mock.activities.synthesis = 1;
  view.rerender(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <PerformanceSettings />
    </QueryClientProvider>,
  );
  expect(screen.getByRole('button', { name: 'runtimeTorch.confirm' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'runtimeTorch.confirm' }));
  await waitFor(() => expect(begin).toHaveBeenCalledExactlyOnceWith('rocm'));
  expect(mock.status.stage).toBe('ready');
  expect(restart).not.toHaveBeenCalled();
  expect(setupRuntime).not.toHaveBeenCalled();
});

it('announces the saved next-start choice from status while the active CPU backend stays ready', async () => {
  mock.status.runtimeTorchSwitchable = true;
  mock.status.runtimeTorchPreference = 'cpu';
  const begin = vi.fn().mockImplementation(async () => {
    mock.status.runtimeTorchPreference = 'rocm';
    mock.status.runtimeTorchSetupPending = true;
  });
  const restart = vi.fn();
  const setupRuntime = vi.fn();
  const cleanSetupRuntime = vi.fn();
  mock.bridge = {
    backend: { beginRuntimeTorchSetup: begin, restart, setupRuntime, cleanSetupRuntime },
  };
  renderPerformance();
  await openRuntimeConfirmation();
  await chooseRuntime();
  fireEvent.click(screen.getByRole('button', { name: 'runtimeTorch.confirm' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('status')).toHaveTextContent('runtimeTorch.pending');
  expect(mock.status.stage).toBe('ready');
  expect(screen.getByRole('button', { name: 'settings.device_family_cpu' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(screen.getByRole('button', { name: 'runtimeTorch.change' })).toBeEnabled();
  expect(restart).not.toHaveBeenCalled();
  expect(setupRuntime).not.toHaveBeenCalled();
  expect(cleanSetupRuntime).not.toHaveBeenCalled();
  expect(mock.api.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0);
  cleanup();
  renderPerformance();
  expect(screen.getByRole('status')).toHaveTextContent('runtimeTorch.pending');
  expect(screen.getByRole('button', { name: 'runtimeTorch.change' })).toBeEnabled();
  await openRuntimeConfirmation();
  expect(screen.getByRole('button', { name: 'runtimeTorch.title' })).toHaveTextContent(
    'runtimeTorch.rocm',
  );
  expect(begin).toHaveBeenCalledOnce();
});

it('shows a pending choice from the supervisor even while switching is temporarily unavailable', () => {
  mock.status.runtimeTorchSetupPending = true;
  mock.status.runtimeTorchPreference = 'rocm';
  mock.status.runtimeTorchSwitchable = false;
  renderPerformance();
  expect(screen.getByRole('status')).toHaveTextContent('runtimeTorch.pending');
  expect(screen.getByRole('button', { name: 'runtimeTorch.change' })).toBeDisabled();
});

it.each([null, 'cuda', 'compute'])(
  'recovers from a CUDA save error (refetch fails: %s)',
  async (failRefetch) => {
    let retried = false;
    mock.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/settings/cuda-device') {
        if (init?.method === 'PUT') throw new Error('Save failed');
        if (retried && failRefetch === 'cuda') throw new Error('Refetch failed');
        return {
          value: 'auto',
          devices: [
            { value: 'GPU-first', index: 0, name: 'First' },
            { value: 'GPU-second', index: 1, name: 'Second' },
          ],
        };
      }
      if (path === '/api/settings/compute-device') {
        if (retried && failRefetch === 'compute') throw new Error('Refetch failed');
        return { value: 'auto', effective_family: 'cuda', available_families: ['cpu', 'cuda'] };
      }
      if (path === '/model/loaded') return { models: [], count: 0 };
      return {};
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PerformanceSettings />
      </QueryClientProvider>,
    );
    const selector = await screen.findByRole('button', { name: 'Choose adapter' });
    await waitFor(() => expect(selector).toBeEnabled());
    fireEvent.click(selector);
    await screen.findByRole('alert');
    retried = true;
    const reads = (endpoint: string) =>
      mock.api.mock.calls.filter(
        ([path, init]) => path === endpoint && (!init?.method || init.method === 'GET'),
      ).length;
    const endpoints = ['/api/settings/compute-device', '/api/settings/cuda-device'];
    const beforeRetry = endpoints.map(reads);
    fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    await waitFor(() =>
      endpoints.forEach((endpoint, index) => {
        expect(reads(endpoint)).toBe(beforeRetry[index] + 1);
      }),
    );
    await waitFor(() => expect(client.isFetching()).toBe(0));
    if (failRefetch) expect(screen.getByRole('alert')).toBeInTheDocument();
    else await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  },
);

it('saves the offload-to-RAM toggle and locks it when an env var pins it (#2618)', async () => {
  let state = { enabled: false, env_pinned: false, device: 'cuda' };
  mock.api.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/settings/perf/offload-after-generation') {
      if (init?.method === 'PUT') {
        state = { ...state, ...(JSON.parse(String(init.body)) as { enabled: boolean }) };
      }
      return state;
    }
    if (path === '/api/settings/cuda-device') return { value: 'auto', devices: [] };
    if (path === '/api/settings/compute-device') {
      return { value: 'auto', effective_family: 'cuda', available_families: ['cpu', 'cuda'] };
    }
    if (path === '/model/loaded') return { models: [], count: 0 };
    return {};
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PerformanceSettings />
    </QueryClientProvider>,
  );
  const toggle = await screen.findByRole('switch', { name: 'settings.offload_after_generation' });
  await waitFor(() => expect(toggle).not.toHaveAttribute('aria-disabled', 'true'));
  expect(toggle).not.toBeChecked();
  expect(screen.getByText('settings.offload_after_generation_note')).toBeInTheDocument();
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(mock.api).toHaveBeenCalledWith(
    '/api/settings/perf/offload-after-generation',
    expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true }) }),
  );

  cleanup();
  state = { enabled: false, env_pinned: true, device: 'cpu' };
  const pinnedClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={pinnedClient}>
      <PerformanceSettings />
    </QueryClientProvider>,
  );
  await screen.findByText('settings.offload_after_generation_cpu');
  expect(screen.getByRole('switch', { name: 'settings.offload_after_generation' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(screen.getByText('settings.generate_timeout_shadowed_note')).toBeInTheDocument();
});
