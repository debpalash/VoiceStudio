import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api, describeError: String }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string }) =>
      options?.name ? `${key}:${options.name}` : key,
  }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children?: React.ReactNode }) => <a>{children}</a>,
}));
import { EngineInstall } from './engine-install';

function renderInstall(props: Partial<Parameters<typeof EngineInstall>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EngineInstall id="moss-soundeffect" name="Sound effects" repairOnly {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => mock.api.mockReset());

it('deletes an app-installed engine after confirmation (#4490)', async () => {
  let installed = true;
  mock.api.mockImplementation(async (_path: string, init?: { method?: string }) => {
    if (init?.method === 'DELETE') {
      installed = false;
      return { status: 'uninstalled' };
    }
    return { installed, managed: installed, repair_available: false, job: null };
  });
  const onRemoved = vi.fn();
  renderInstall({ onRemoved });

  fireEvent.click(await screen.findByRole('button', { name: 'modelMaintenance.delete' }));
  // Nothing in repair-only mode offers an Install button for a healthy engine.
  expect(screen.queryByRole('button', { name: 'modelMaintenance.install' })).toBeNull();
  const dialog = await screen.findByRole('dialog');
  expect(
    within(dialog).getByText('modelMaintenance.engineDeleteConfirm:Sound effects'),
  ).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'modelMaintenance.delete' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/engines/sidecar/moss-soundeffect/install', {
      method: 'DELETE',
    }),
  );
  await waitFor(() => expect(onRemoved).toHaveBeenCalled());
});

it("never offers to delete a user's own clone", async () => {
  mock.api.mockResolvedValue({
    installed: true,
    managed: false,
    repair_available: false,
    job: null,
  });
  const { container } = renderInstall();
  await waitFor(() => expect(mock.api).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: 'modelMaintenance.delete' })).toBeNull();
  expect(container.textContent).toBe('');
});

it('shows why a delete failed', async () => {
  mock.api.mockImplementation(async (_path: string, init?: { method?: string }) => {
    if (init?.method === 'DELETE') throw new Error('files are in use');
    return { installed: true, managed: true, repair_available: false, job: null };
  });
  renderInstall();

  fireEvent.click(await screen.findByRole('button', { name: 'modelMaintenance.delete' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'modelMaintenance.delete' }));

  expect(await screen.findByText('modelMaintenance.engineDeleteFailed')).toBeTruthy();
  expect(screen.getByText(/files are in use/)).toBeTruthy();
});
