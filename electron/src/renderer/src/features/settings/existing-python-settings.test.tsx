import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExistingPythonSettings } from './existing-python-settings';
const backend = vi.hoisted(() => ({
  getPythonRuntime: vi.fn(), discoverPythonRuntimes: vi.fn(),
  useDiscoveredPythonRuntime: vi.fn(), choosePythonRuntime: vi.fn(), useManagedPython: vi.fn(),
  confirmPythonRuntime: vi.fn(), planPythonRepair: vi.fn(), installPythonRepair: vi.fn(),
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => ({ backend }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
const candidate = { pythonPath: '/fixture/.venv/bin/python', source: 'venv', version: '3.14.3' };
const found = { candidates: [candidate], warnings: [], partial: false, scannedRoots: ['/fixture'] };
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  backend.getPythonRuntime.mockResolvedValue({ selection: null, overrideActive: false });
  backend.discoverPythonRuntimes.mockResolvedValue(found);
  backend.useDiscoveredPythonRuntime.mockResolvedValue({ ok: true, token: 'selection-token', pythonPath: candidate.pythonPath, version: '3.14.3' });
  backend.confirmPythonRuntime.mockResolvedValue({ ok: true, pythonPath: candidate.pythonPath, version: '3.14.3' });
  backend.choosePythonRuntime.mockResolvedValue({ canceled: true, ok: false });
});
async function search() {
  render(<ExistingPythonSettings />);
  await waitFor(() => expect(backend.getPythonRuntime).toHaveBeenCalled());
  fireEvent.click(screen.getByTestId('python-runtime-quick-search'));
}
it('lists paths and metadata without selecting, while retaining manual selection', async () => {
  await search();
  expect(await screen.findByText(candidate.pythonPath)).toBeVisible();
  expect(screen.getByText('3.14.3')).toBeVisible();
  expect(screen.getByText('settings.python_runtime_search_scope')).toBeVisible();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose' })).toBeEnabled();
  expect(backend.useDiscoveredPythonRuntime).not.toHaveBeenCalled();
});
it('shows permission and incomplete-result warnings alongside readable candidates', async () => {
  backend.discoverPythonRuntimes.mockResolvedValue({ ...found, partial: true, warnings: [{ path: '/denied', reason: 'permission_denied' }] });
  await search();
  expect(await screen.findByText('settings.python_runtime_permission_warning')).toBeVisible();
  // The permission-specific warning already states that results may be incomplete.
  expect(screen.queryByText('settings.python_runtime_partial_warning')).toBeNull();
  expect(screen.getByText(candidate.pythonPath)).toBeVisible();
});
it('shows the limit warning even if some environments were found', async () => {
  backend.discoverPythonRuntimes.mockResolvedValue({ ...found, partial: true, warnings: [{ path: '/large', reason: 'scan_limit' }] });
  await search();
  expect(await screen.findByText('settings.python_runtime_partial_warning')).toBeVisible();
  expect(screen.queryByText('settings.python_runtime_permission_warning')).toBeNull();
});
it('shows empty results without removing the manual picker', async () => {
  backend.discoverPythonRuntimes.mockResolvedValue({ ...found, candidates: [] });
  await search();
  expect(await screen.findByText('settings.python_runtime_empty')).toBeVisible();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose' })).toBeEnabled();
});
it('reports scan errors and restores enabled controls', async () => {
  backend.discoverPythonRuntimes.mockRejectedValue(new Error('scan_failed'));
  await search();
  expect(await screen.findByText('scan_failed')).toBeVisible();
  expect(screen.getByTestId('python-runtime-quick-search')).toBeEnabled();
});
it('does not allow repeated scans or manual choices while scanning', async () => {
  let finish!: (result: typeof found) => void;
  backend.discoverPythonRuntimes.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await search();
  expect(screen.getByTestId('python-runtime-quick-search')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose' })).toBeDisabled();
  fireEvent.click(screen.getByTestId('python-runtime-quick-search'));
  expect(backend.discoverPythonRuntimes).toHaveBeenCalledTimes(1);
  await act(async () => finish(found));
});
it('stages a validated discovery result without refreshing or saving selection', async () => {
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  expect(await screen.findByRole('button', { name: 'settings.python_runtime_confirm' })).toBeEnabled();
  expect(screen.getByText('settings.python_runtime_ready')).toBeVisible();
  expect(backend.useDiscoveredPythonRuntime).toHaveBeenCalledWith(candidate.pythonPath);
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
  expect(backend.choosePythonRuntime).not.toHaveBeenCalled();
});
it('stages the native picker result without changing the previously saved selection', async () => {
  backend.getPythonRuntime.mockResolvedValue({ selection: { pythonPath: '/previous/bin/python' }, overrideActive: false });
  backend.choosePythonRuntime.mockResolvedValue({ ok: true, token: 'manual-token', pythonPath: candidate.pythonPath, version: '3.14.3' });
  render(<ExistingPythonSettings />);
  fireEvent.click(screen.getByRole('button', { name: 'settings.python_runtime_choose' }));
  expect(await screen.findByRole('button', { name: 'settings.python_runtime_confirm' })).toBeEnabled();
  expect(screen.getByText('/previous/bin/python')).toBeVisible();
  expect(screen.getByText(candidate.pythonPath)).toBeVisible();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
});
it('reports missing dependencies instead of treating discovery as compatibility', async () => {
  backend.useDiscoveredPythonRuntime.mockResolvedValue({ ok: false, missingModules: ['torch'] });
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  expect(await screen.findByText('settings.python_runtime_missing')).toBeVisible();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
});
it('requires a reviewed plan before any install and allows choosing another interpreter', async () => {
  backend.useDiscoveredPythonRuntime.mockResolvedValue({ ok: false, pythonPath: candidate.pythonPath, version: '3.14.3', missingModules: ['torch'], repairPlan: { interpreter: candidate.pythonPath, packages: ['torch>=2.10.0'] } });
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  expect(await screen.findByRole('button', { name: 'settings.python_runtime_review_install' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose_another' })).toBeEnabled();
  expect(backend.planPythonRepair).not.toHaveBeenCalled();
  expect(backend.installPythonRepair).not.toHaveBeenCalled();
  expect(backend.confirmPythonRuntime).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'settings.python_runtime_confirm' })).toBeNull();
});
it('does not offer package installation when no reviewed dependency mapping exists', async () => {
  backend.useDiscoveredPythonRuntime.mockResolvedValue({ ok: false, pythonPath: candidate.pythonPath, missingModules: ['unreviewed_import'] });
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  expect(await screen.findByText('settings.python_runtime_repair_blocked')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'settings.python_runtime_review_install' })).toBeNull();
  expect(backend.installPythonRepair).not.toHaveBeenCalled();
});
const repairPlan = { interpreter: candidate.pythonPath, packages: ['torch>=2.10.0'] };
const missing = { ok: false, token: 'candidate-token', pythonPath: candidate.pythonPath, version: '3.14.3', missingModules: ['torch'], repairPlan };
async function reviewRepair() {
  backend.useDiscoveredPythonRuntime.mockResolvedValue(missing);
  backend.planPythonRepair.mockResolvedValue({ ...missing, repairToken: 'repair-token' });
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  fireEvent.click(await screen.findByRole('button', { name: 'settings.python_runtime_review_install' }));
  await screen.findByRole('checkbox', { name: 'settings.python_runtime_install_consent' });
}
it('reviews exact target and packages, and denial never starts installation', async () => {
  await reviewRepair();
  expect(backend.planPythonRepair).toHaveBeenCalledWith('candidate-token');
  expect(screen.getByTestId('python-runtime-repair-plan')).toHaveTextContent(candidate.pythonPath);
  expect(screen.getByTestId('python-runtime-repair-plan')).toHaveTextContent('torch>=2.10.0');
  expect(screen.getByRole('button', { name: 'settings.python_runtime_install_confirm' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(backend.installPythonRepair).not.toHaveBeenCalled();
  expect(backend.confirmPythonRuntime).not.toHaveBeenCalled();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
});
it('installs only the confirmed reviewed target and packages, then still waits for selection confirmation', async () => {
  backend.installPythonRepair.mockResolvedValue({ ok: true, pythonPath: candidate.pythonPath, version: '3.14.3', token: 'revalidated-token', installed: true });
  await reviewRepair();
  fireEvent.click(screen.getByRole('checkbox', { name: 'settings.python_runtime_install_consent' }));
  fireEvent.click(screen.getByRole('button', { name: 'settings.python_runtime_install_confirm' }));
  expect(await screen.findByText('settings.python_runtime_ready')).toBeVisible();
  expect(backend.installPythonRepair).toHaveBeenCalledWith({ token: 'repair-token', pythonPath: candidate.pythonPath, packages: repairPlan.packages });
  expect(backend.confirmPythonRuntime).not.toHaveBeenCalled();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'settings.python_runtime_confirm' }));
  await waitFor(() => expect(backend.confirmPythonRuntime).toHaveBeenCalledWith('revalidated-token'));
});
it.each([
  ['rejected', new Error('pip_failed')],
  ['failed revalidation', { ...missing, installed: true }],
  ['canceled', { ...missing, canceled: true, installed: false }],
])('keeps the saved selection unchanged after %s installation', async (_label, outcome) => {
  backend.getPythonRuntime.mockResolvedValue({ selection: { pythonPath: '/previous/bin/python' }, overrideActive: false });
  if (outcome instanceof Error) backend.installPythonRepair.mockRejectedValue(outcome);
  else backend.installPythonRepair.mockResolvedValue(outcome);
  await reviewRepair();
  fireEvent.click(screen.getByRole('checkbox', { name: 'settings.python_runtime_install_consent' }));
  fireEvent.click(screen.getByRole('button', { name: 'settings.python_runtime_install_confirm' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'settings.python_runtime_choose_another' })).toBeEnabled());
  if (_label !== 'canceled') expect(await screen.findByText(/python_runtime_repair_failed/)).toBeVisible();
  else expect(screen.queryByText(/python_runtime_repair_failed/)).toBeNull();
  if (outcome instanceof Error) expect(screen.getByText(/pip_failed/)).toBeVisible();
  expect(screen.getByText('/previous/bin/python')).toBeVisible();
  expect(backend.confirmPythonRuntime).not.toHaveBeenCalled();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.queryByRole('button', { name: 'settings.python_runtime_confirm' })).toBeNull();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose_another' })).toBeEnabled();
});
it('blocks all selection controls while installation is running and does not save on completion', async () => {
  let finish!: (result: unknown) => void;
  backend.installPythonRepair.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await reviewRepair();
  fireEvent.click(screen.getByRole('checkbox', { name: 'settings.python_runtime_install_consent' }));
  fireEvent.click(screen.getByRole('button', { name: 'settings.python_runtime_install_confirm' }));
  expect(await screen.findByText('settings.python_runtime_installing')).toBeVisible();
  expect(screen.getByTestId('python-runtime-quick-search')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose' })).toBeDisabled();
  expect(screen.getByTestId('python-runtime-candidate')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'settings.python_runtime_choose_another' })).toBeDisabled();
  expect(backend.installPythonRepair).toHaveBeenCalledTimes(1);
  await act(async () => finish({ ok: true, pythonPath: candidate.pythonPath, token: 'repaired-token' }));
  expect(screen.getByRole('button', { name: 'settings.python_runtime_confirm' })).toBeEnabled();
  expect(backend.confirmPythonRuntime).not.toHaveBeenCalled();
});
it('handles a blocked main repair plan without exposing an install confirmation', async () => {
  backend.useDiscoveredPythonRuntime.mockResolvedValue(missing);
  backend.planPythonRepair.mockResolvedValue({ ...missing, repairPlan: undefined, detail: 'externally_managed' });
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  fireEvent.click(await screen.findByRole('button', { name: 'settings.python_runtime_review_install' }));
  await waitFor(() => expect(backend.planPythonRepair).toHaveBeenCalled());
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.getAllByText('settings.python_runtime_repair_blocked').length).toBeGreaterThan(0);
  expect(backend.installPythonRepair).not.toHaveBeenCalled();
});
it('clears a consumed selection token after failed confirmation while keeping the previous saved selection', async () => {
  backend.getPythonRuntime.mockResolvedValue({ selection: { pythonPath: '/previous/bin/python' }, overrideActive: false });
  backend.confirmPythonRuntime.mockRejectedValue(new Error('python_confirmation_expired'));
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  fireEvent.click(await screen.findByRole('button', { name: 'settings.python_runtime_confirm' }));
  expect(await screen.findByText('python_confirmation_expired')).toBeVisible();
  expect(screen.getByText('/previous/bin/python')).toBeVisible();
  expect(screen.queryByTestId('python-runtime-staged')).toBeNull();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(1);
});
it('commits the main-owned selection token only on explicit confirmation', async () => {
  await search();
  fireEvent.click(await screen.findByTestId('python-runtime-candidate'));
  const confirm = await screen.findByRole('button', { name: 'settings.python_runtime_confirm' });
  expect(backend.confirmPythonRuntime).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(backend.confirmPythonRuntime).toHaveBeenCalledWith('selection-token'));
  expect(await screen.findByText('settings.python_runtime_saved')).toBeVisible();
  expect(backend.getPythonRuntime).toHaveBeenCalledTimes(2);
  expect(screen.queryByTestId('python-runtime-staged')).toBeNull();
});
it('cancelling the manual picker adds no validation error', async () => {
  render(<ExistingPythonSettings />);
  fireEvent.click(screen.getByRole('button', { name: 'settings.python_runtime_choose' }));
  await waitFor(() => expect(backend.choosePythonRuntime).toHaveBeenCalled());
  expect(screen.queryByText('settings.python_runtime_invalid')).toBeNull();
});
