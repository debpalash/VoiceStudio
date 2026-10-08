import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { SystemPreflight } from './system-preflight';
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SystemPreflight />
    </QueryClientProvider>,
  );
}
it('runs only on explicit request and renders backend diagnostics and fixes', async () => {
  mock.api.mockResolvedValue({
    checks: [
      { id: 'ram', label: 'Memory', status: 'warn', detail: '8 GB', fix: 'Use a smaller model' },
    ],
  });
  mount();
  expect(mock.api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'setup.system_check' }));
  await screen.findByText('Use a smaller model');
  expect(mock.api).toHaveBeenCalledWith(
    '/setup/preflight',
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'setup.recheck' }));
  await waitFor(() => expect(mock.api).toHaveBeenCalledTimes(2));
});
it('shows failure and permits an explicit retry without automatic probes', async () => {
  mock.api.mockRejectedValue(new Error('unreachable'));
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'setup.system_check' }));
  await screen.findByRole('alert');
  expect(mock.api).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'setup.system_check' })).toBeEnabled();
});
it('renders the Windows-on-ARM notice from the translation catalog, not backend text', async () => {
  mock.api.mockResolvedValue({
    checks: [
      {
        id: 'arch',
        label: 'Windows on ARM',
        status: 'warn',
        detail: 'English detail',
        fix: 'English fix',
      },
    ],
  });
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'setup.system_check' }));
  await screen.findByText('setup.check_arch_label');
  expect(screen.getByText('setup.check_arch_detail')).toBeTruthy();
  expect(screen.getByText('setup.check_arch_fix')).toBeTruthy();
  expect(screen.queryByText('English detail')).toBeNull();
});
