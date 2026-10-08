import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@/i18n';

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  remove: vi.fn(),
  detach: vi.fn(),
  edit: vi.fn(),
  active: false,
  drafts: { stories: { projectId: 'book' }, audiobook: { projectId: null } },
}));
vi.mock('@/components/workspace-sidebar', () => ({
  SecondarySidebar: ({ children }: any) => <aside>{children}</aside>,
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: any) => <header>{children}</header>,
}));
vi.mock('@/components/audio-preview-button', () => ({ AudioPreviewButton: () => null }));
vi.mock('@/components/pipeline-failure', () => ({
  PipelineFailure: ({ fallback }: any) => <p role="alert">{fallback}</p>,
}));
vi.mock('@/components/profile-avatar', () => ({ ProfileAvatar: () => null }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({ data: [] }),
  useDeleteProfile: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock('@/hooks/use-history', () => ({ useHistory: () => ({ data: [] }) }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  apiPath: (p: string) => p,
  describeError: (e: Error) => e.message,
}));
vi.mock('../dub/dub-session', () => ({
  useDubSession: () => ({ phase: mocks.active ? 'generating' : 'idle' }),
  detachDubProject: mocks.detach,
  attachDubProject: vi.fn(),
  openDubProject: vi.fn(),
}));
vi.mock('../longform/longform-session', () => ({
  useLongformSession: () => ({ active: mocks.active }),
  longformSession: { state: { active: false, drafts: mocks.drafts } },
  blankLongformDraft: () => ({}),
  editLongform: mocks.edit,
}));
vi.mock('../longform/project-library', () => ({
  projectLibrary: {
    list: async () => [{ id: 'book', mode: 'stories', name: 'Saved story', updatedAt: 1 }],
    remove: mocks.remove,
    rename: vi.fn(),
  },
}));
import { ProjectsPage } from './projects-page';

let exports: { id: string; filename: string; destination_path: string }[];
let dubs: { id: string; name: string }[];
let clients: QueryClient[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocks.active = false;
  exports = [{ id: 'same', filename: 'Exported audio', destination_path: '/kept/audio.wav' }];
  dubs = [{ id: 'same', name: 'Dub project' }];
  mocks.api.mockImplementation(async (path: string, options?: { method?: string }) => {
    if (options?.method === 'DELETE') {
      if (path === '/export/history/same') exports = [];
      if (path === '/projects/same') dubs = [];
      return {};
    }
    if (path === '/projects') return dubs;
    if (path === '/export/history') return exports;
    if (path === '/longform/jobs') return { jobs: [] };
    return {};
  });
});
afterEach(() => {
  cleanup();
  clients.forEach((c) => c.clear());
  clients = [];
});
async function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ProjectsPage />
    </QueryClientProvider>,
  );
  await screen.findByRole('checkbox', { name: 'Select Exported audio' });
}
function deletions() {
  return mocks.api.mock.calls.filter(([, options]) => options?.method === 'DELETE');
}

it('confirms single export deletion, supports cancellation, and refreshes the list', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Exported audio' }));
  let dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText(/Exported files.*kept/)).toBeTruthy();
  expect(deletions()).toHaveLength(0);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(deletions()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Delete Exported audio' }));
  dialog = screen.getByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
  await waitFor(() =>
    expect(screen.queryByRole('checkbox', { name: 'Select Exported audio' })).toBeNull(),
  );
  expect(deletions().map(([path]) => path)).toEqual(['/export/history/same']);
  expect(screen.getByRole('checkbox', { name: 'Select Dub project' })).toBeTruthy();
});

it('bulk deletion retains failures for retry and does not repeat successful deletes', async () => {
  await mount();
  const original = mocks.api.getMockImplementation()!;
  let fail = true;
  mocks.api.mockImplementation(async (...args) => {
    if (args[0] === '/export/history/same' && args[1]?.method === 'DELETE' && fail)
      throw new Error('Disk unavailable');
    return original(...args);
  });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Dub project' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Exported audio' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected (2)' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('alert').textContent).toContain('Disk unavailable');
  expect(within(screen.getByRole('dialog')).queryByText('Dub project')).toBeNull();
  fail = false;
  await waitFor(() =>
    expect(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Retry' }),
    ).not.toBeDisabled(),
  );
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(deletions().filter(([path]) => path === '/projects/same')).toHaveLength(1);
  expect(mocks.detach).toHaveBeenCalledWith('same');
});

it('clears hidden selections when searching and selects only visible rows', async () => {
  await mount();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Dub project' }));
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Exported' } });
  expect(screen.queryByRole('button', { name: /Delete selected/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Select visible' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected (1)' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(deletions().map(([path]) => path)).toEqual(['/export/history/same']);
});

it('detaches deleted longform drafts and blocks deletion during generation', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Saved story' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(mocks.edit).toHaveBeenCalledWith('stories', { projectId: null }));
  expect(mocks.remove).toHaveBeenCalledWith('book');
  cleanup();
  mocks.active = true;
  await mount();
  expect(screen.getByRole('button', { name: 'Delete Dub project' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Select visible' })).toBeDisabled();
});

it('renaming a dub never issues a deletion', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: /Rename Dub project/i }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Renamed' } });
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  await waitFor(() =>
    expect(mocks.api).toHaveBeenCalledWith('/projects/same', {
      method: 'PATCH',
      body: JSON.stringify({ name: 'Renamed' }),
    }),
  );
  expect(deletions()).toHaveLength(0);
});

it('the Enter that commits an IME composition does not submit a rename', async () => {
  await mount();
  fireEvent.click(screen.getByRole('button', { name: /Rename Dub project/i }));
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: 'Renamed' } });
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
  expect(mocks.api.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(0);
  expect(screen.getByRole('textbox')).toBe(input);
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() =>
    expect(mocks.api).toHaveBeenCalledWith('/projects/same', {
      method: 'PATCH',
      body: JSON.stringify({ name: 'Renamed' }),
    }),
  );
});

it('prevents duplicate submissions and cancellation while deletion is pending', async () => {
  await mount();
  const original = mocks.api.getMockImplementation()!;
  let finish!: () => void;
  const gate = new Promise<void>((resolve) => {
    finish = resolve;
  });
  mocks.api.mockImplementation(async (...args) => {
    if (args[1]?.method === 'DELETE') await gate;
    return original(...args);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Delete Dub project' }));
  const dialog = screen.getByRole('dialog');
  const remove = within(dialog).getByRole('button', { name: 'Delete' });
  fireEvent.click(remove);
  fireEvent.click(remove);
  expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(deletions()).toHaveLength(1);
  finish();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});
