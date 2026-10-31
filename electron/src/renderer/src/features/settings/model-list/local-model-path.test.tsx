import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn(), choose: vi.fn(), native: true }));
vi.mock('@/lib/api/client', async (load) => ({
  ...await load<typeof import('@/lib/api/client')>(), apiJson: mock.api,
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => mock.native ? { files: { chooseEngineModelDirectory: mock.choose } } : null,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { LocalModelPath } from './local-model-path';
import { ApiError } from '@/lib/api/client';

beforeEach(() => {
  mock.native = true;
  mock.api.mockResolvedValue({ path: '/models/local' });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function mount(engineId = 'omnivoice', family: 'tts' | 'asr' = 'tts') {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <LocalModelPath family={family} engineId={engineId} />
  </QueryClientProvider>);
}

it('writes only the native capability and refreshes the saved path', async () => {
  mock.choose.mockResolvedValue({ authorization: 'one-shot', path: '/private/model' });
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelChoose' }));
  await waitFor(() => expect(mock.api).toHaveBeenCalledWith('/api/settings/engines/tts/omnivoice/local-model', {
    method: 'PUT', body: JSON.stringify({ authorization: 'one-shot' }),
  }));
  expect(mock.choose).toHaveBeenCalledWith('omnivoice');
  await waitFor(() => expect(mock.api.mock.calls.filter(([, init]) => !init?.method).length).toBe(2));
});

it('cancel does not mutate and reset uses DELETE without a filesystem path', async () => {
  mock.choose.mockResolvedValue(null);
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelChoose' }));
  await waitFor(() => expect(mock.choose).toHaveBeenCalled());
  expect(mock.api.mock.calls.some(([, init]) => init?.method)).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelReset' }));
  await waitFor(() => expect(mock.api).toHaveBeenCalledWith('/api/settings/engines/tts/omnivoice/local-model', { method: 'DELETE' }));
});

it('shows mutation errors and keeps the persisted path visible', async () => {
  mock.choose.mockResolvedValue({ authorization: 'token' });
  mock.api.mockImplementation((_url, init) => init?.method ? Promise.reject(new Error('engine busy')) : Promise.resolve({ path: '/models/local' }));
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelChoose' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('modelSettings.localModelError');
  expect(screen.getByText('/models/local')).toBeInTheDocument();
});

it('preserves actionable model diagnostics alongside the localized error', async () => {
  mock.choose.mockResolvedValue({ authorization: 'token' });
  mock.api.mockImplementation((_url, init) => init?.method
    ? Promise.reject(new ApiError(400, 'The audio tokenizer is missing preprocessor_config.json'))
    : Promise.resolve({ path: '/models/local' }));
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelChoose' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('preprocessor_config.json');
});

it('does not disguise offline GET errors as the default model', async () => {
  mock.api.mockRejectedValue(new Error('backend offline'));
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent('modelSettings.localModelReadError');
  expect(screen.queryByText('modelSettings.localModelDefault')).not.toBeInTheDocument();
});

it('displays the authoritative environment override separately from the saved folder', async () => {
  mock.api.mockResolvedValue({ path: '/models/local', environment_override: 'OMNIVOICE_MODEL', effective_override: 'operator/model' });
  mount();
  await screen.findByText('/models/local');
  expect(screen.getByText(/OMNIVOICE_MODEL/)).toHaveTextContent('operator/model');
  expect(screen.getByText(/modelSettings.localModelEnvironmentOverride/)).toBeInTheDocument();
});

it.each([['piper', 'tts'], ['omnivoice', 'asr']] as const)('hides unsupported %s/%s without API traffic', (engine, family) => {
  mount(engine, family);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(mock.api).not.toHaveBeenCalled();
});

it('hides browser-only selection without querying private host paths', () => {
  mock.native = false;
  mount();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(mock.api).not.toHaveBeenCalled();
});

it('disables duplicate writes while native selection is pending', async () => {
  mock.choose.mockReturnValue(new Promise(() => {}));
  mount('mlx-audio');
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelChoose' }));
  expect(screen.getByRole('button', { name: 'modelSettings.localModelChoose' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'modelSettings.localModelReset' })).toBeDisabled();
});

it('searches with a dedicated label and explicitly saves only a discovered exact-candidate grant', async () => {
  mock.choose.mockResolvedValue({ authorization: 'parent-grant', path: '/models' });
  mock.api.mockImplementation((_url, init) => init?.method === 'POST' ? Promise.resolve({ candidates: [{ name: 'voice', path: '/models/voice', model_name: 'Voice', validation: { status: 'valid', code: 'complete', message: 'RAW BACKEND MESSAGE' }, authorization: 'candidate-grant' }] }) : Promise.resolve({ path: '/models/local' }));
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelSearch' }));
  expect(await screen.findByText('/models/voice')).toBeInTheDocument();
  expect(screen.queryByText('RAW BACKEND MESSAGE')).not.toBeInTheDocument();
  expect(mock.api.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'modelSettings.localModelUse' }));
  await waitFor(() => expect(mock.api).toHaveBeenCalledWith('/api/settings/engines/tts/omnivoice/local-model', { method: 'PUT', body: JSON.stringify({ authorization: 'candidate-grant' }) }));
  expect(await screen.findByText('modelSettings.localModelSaved')).toBeInTheDocument();
});

it('reports empty and partial discovery independently, even with no candidates', async () => {
  mock.choose.mockResolvedValue({ authorization: 'parent' });
  mock.api.mockImplementation((_url, init) => Promise.resolve(init?.method === 'POST' ? { candidates: [], limited: true } : { path: '/models/local' }));
  mount();
  fireEvent.click(await screen.findByTestId('local-model-discover'));
  expect(await screen.findByText('modelSettings.localModelEmpty')).toBeInTheDocument();
  expect(screen.getByText('modelSettings.localModelPartial')).toBeInTheDocument();
  expect(screen.queryByTestId('local-model-candidates')).not.toBeInTheDocument();
});

it('disables invalid and ungranted candidates and bounds results with wrapping full paths', async () => {
  mock.choose.mockResolvedValue({ authorization: 'parent' });
  mock.api.mockImplementation((_url, init) => Promise.resolve(init?.method === 'POST' ? { candidates: [
    { name: 'bad', path: '/models/bad/full/path', validation: { status: 'invalid', code: 'missing_assets', message: 'RAW' }, authorization: 'unusable' },
    { name: 'ungranted', path: '/models/ungranted', validation: { status: 'valid', code: 'complete' } },
  ] } : { path: '/models/local' }));
  mount();
  fireEvent.click(await screen.findByTestId('local-model-discover'));
  expect(await screen.findByText('/models/bad/full/path')).toHaveClass('break-all');
  expect(screen.getByTestId('local-model-candidates')).toHaveClass('max-h-64', 'overflow-y-auto');
  for (const button of screen.getAllByRole('button', { name: 'modelSettings.localModelUse' })) expect(button).toBeDisabled();
  expect(screen.getByText('modelSettings.localModelValidationAssets')).toBeInTheDocument();
  expect(screen.queryByText('RAW')).not.toBeInTheDocument();
});

it('keeps settings and previous results unchanged when the native search picker is cancelled', async () => {
  mock.choose.mockResolvedValue(null);
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByTestId('local-model-discover'));
  expect(await screen.findByText('modelSettings.localModelCancelled')).toBeInTheDocument();
  expect(mock.api.mock.calls.some(([, init]) => init?.method)).toBe(false);
  expect(screen.getByText('/models/local')).toHaveClass('break-all');
});

it('shows a spinner and prevents all duplicate actions while search is in flight', async () => {
  mock.choose.mockReturnValue(new Promise(() => {}));
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByTestId('local-model-discover'));
  expect(screen.getByText('modelSettings.localModelSearching')).toBeInTheDocument();
  expect(screen.getByText('modelSettings.localModelSearching').querySelector('svg')).toHaveClass('animate-spin');
  for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
});

it('localizes engine busy without claiming the selection was saved', async () => {
  mock.choose.mockResolvedValue({ authorization: 'grant' });
  mock.api.mockImplementation((_url, init) => init?.method ? Promise.reject(new ApiError(409, 'RAW engine busy')) : Promise.resolve({ path: '/models/local' }));
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByTestId('local-model-choose'));
  expect(await screen.findByRole('alert')).toHaveTextContent('modelSettings.localModelBusy');
  expect(screen.queryByText('modelSettings.localModelSaved')).not.toBeInTheDocument();
  expect(screen.getByTestId('local-model-choose')).toBeEnabled();
});

it('removes consumed candidate grants after a failed save and preserves the stored model', async () => {
  mock.choose.mockResolvedValue({ authorization: 'parent' });
  mock.api.mockImplementation((_url, init) => {
    if (init?.method === 'POST') return Promise.resolve({ candidates: [{ name: 'voice', path: '/models/voice', validation: { status: 'valid', code: 'complete' }, authorization: 'one-shot' }] });
    if (init?.method === 'PUT') return Promise.reject(new ApiError(400, 'expired grant'));
    return Promise.resolve({ path: '/models/local' });
  });
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByTestId('local-model-discover'));
  fireEvent.click(await screen.findByRole('button', { name: 'modelSettings.localModelUse' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('modelSettings.localModelError');
  expect(screen.queryByTestId('local-model-candidates')).not.toBeInTheDocument();
  expect(screen.getByText('/models/local')).toBeInTheDocument();
  expect(screen.getByTestId('local-model-discover')).toBeEnabled();
});

it('reports discovery failure without claiming the search was empty', async () => {
  mock.choose.mockResolvedValue({ authorization: 'parent' });
  mock.api.mockImplementation((_url, init) => init?.method === 'POST' ? Promise.reject(new Error('permission denied')) : Promise.resolve({ path: '/models/local' }));
  mount();
  await screen.findByText('/models/local');
  fireEvent.click(screen.getByTestId('local-model-discover'));
  expect(await screen.findByRole('alert')).toHaveTextContent('modelSettings.localModelError');
  expect(screen.queryByText('modelSettings.localModelEmpty')).not.toBeInTheDocument();
  expect(screen.getByTestId('local-model-discover')).toBeEnabled();
});

it.each([
  ['loaded', 'modelSettings.localModelLoadingLoaded'],
  ['not_loaded', 'modelSettings.localModelLoadingNotLoaded'],
  ['loading', 'modelSettings.localModelLoadingLoading'],
  ['failed', 'modelSettings.localModelLoadingFailed'],
  ['unexpected_backend_status', 'modelSettings.localModelLoadingUnknown'],
])('localizes runtime status %s without exposing raw backend text', async (status, key) => {
  mock.api.mockResolvedValue({ path: '/models/local', loading: { status }, validation: { status: 'invalid', code: 'unexpected', message: 'RAW VALIDATION' } });
  mount();
  expect(await screen.findByTestId('local-model-loading')).toHaveTextContent(key);
  expect(screen.getByTestId('local-model-validation')).toHaveTextContent('modelSettings.localModelValidationUnknown');
  expect(screen.queryByText('RAW VALIDATION')).not.toBeInTheDocument();
});
