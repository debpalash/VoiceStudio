import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const mocks = vi.hoisted(() => ({
  json: vi.fn(),
  fetch: vi.fn(),
  save: vi.fn(),
  list: vi.fn(),
  download: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.json,
  apiFetch: mocks.fetch,
  describeError: (error: unknown) => (error as { message?: string } | null | undefined)?.message ?? '',
}));
vi.mock('@/lib/local-export', () => ({ saveLocalFile: mocks.download }));
vi.mock('../longform/project-library', () => ({
  projectLibrary: { save: mocks.save, list: mocks.list },
}));
import { ProjectHandoff } from './project-handoff';
let client: QueryClient;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.json.mockImplementation(async (path: string) =>
    path === '/project-bundles'
      ? []
      : {
          kind: 'stories',
          title: 'Imported',
          project: { draft: { script: 'Phone edit', lines: [], cast: [], projectId: 'foreign' } },
        },
  );
});
afterEach(() => {
  cleanup();
  client.clear();
});
function mount() {
  return render(
    <QueryClientProvider client={client}>
      <ProjectHandoff disabled={false} />
    </QueryClientProvider>,
  );
}
it('opens the paired inbox only on request and sends an explicit voice-permission choice with file imports', async () => {
  mount();
  expect(mocks.json).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('mobile.handoff.title'));
  await waitFor(() =>
    expect(mocks.json).toHaveBeenCalledWith('/project-bundles', expect.anything()),
  );
  const checkbox = screen.getByRole('checkbox');
  expect(checkbox).not.toBeChecked();
  fireEvent.click(checkbox);
  const file = new File(['archive'], 'book.vsproject', { type: 'application/zip' });
  fireEvent.change(document.querySelector('input[type=file]')!, { target: { files: [file] } });
  await waitFor(() =>
    expect(mocks.json).toHaveBeenCalledWith(
      '/project-bundles/import?allow_voices=true',
      expect.objectContaining({ method: 'POST', body: file }),
    ),
  );
  await waitFor(() =>
    expect(mocks.save).toHaveBeenCalledWith(
      'Imported',
      'stories',
      expect.objectContaining({ script: 'Phone edit', projectId: null }),
    ),
  );
});
it('does not grant voice permission when the checkbox is left off', async () => {
  mount();
  fireEvent.click(screen.getByText('mobile.handoff.title'));
  fireEvent.change(document.querySelector('input[type=file]')!, {
    target: { files: [new File(['archive'], 'book.vsproject')] },
  });
  await waitFor(() =>
    expect(mocks.json).toHaveBeenCalledWith(
      '/project-bundles/import?allow_voices=false',
      expect.anything(),
    ),
  );
});
