import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  navigate: vi.fn(),
  dubs: [] as unknown[],
  longform: {
    active: null as string | null,
    drafts: { stories: { projectId: null }, audiobook: { projectId: 'b2' } },
  },
  dub: { project: null as { id: string } | null, phase: 'idle', recovery: null, instruct: '' },
  openDub: vi.fn(() => true),
  readProject: vi.fn<(path: string, options?: { signal?: AbortSignal }) => Promise<unknown>>(),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => state.navigate,
  Link: ({ children, to }: { children: unknown; to: string }) => (
    <a href={to}>{children as never}</a>
  ),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: async (path: string, options?: { signal?: AbortSignal }) =>
    path === '/projects' ? state.dubs : state.readProject(path, options),
  describeError: (error: unknown) => String(error),
}));
vi.mock('../longform/longform-session', () => ({
  useLongformSession: () => state.longform,
  longformSession: {
    get state() {
      return state.longform;
    },
  },
}));
vi.mock('./dub-session', () => ({
  useDubSession: () => state.dub,
  dubSession: {
    get state() {
      return state.dub;
    },
  },
  openDubProject: state.openDub,
}));
import { DubProjects } from './dub-projects';

const renderList = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = (active: boolean) => (
    <QueryClientProvider client={client}>
      <DubProjects active={active} />
    </QueryClientProvider>
  );
  const view = render(content(true));
  return { ...view, setActive: (active: boolean) => view.rerender(content(active)) };
};

async function confirmProject(name: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name }));
  fireEvent.click(screen.getByRole('button', { name: 'common.confirm' }));
}

beforeEach(() => {
  state.navigate.mockReset();
  state.openDub.mockReset().mockReturnValue(true);
  state.readProject
    .mockReset()
    .mockImplementation(async (path) =>
      state.dubs.find((p) => path.endsWith((p as { id: string }).id)),
    );
  state.longform.active = null;
  state.dub = { project: { id: 'd2' }, phase: 'idle', recovery: null, instruct: '' };
  state.dubs = [
    { id: 'd1', name: 'Launch video', updated_at: 1_700_000_000 },
    { id: 'd2', name: 'Podcast intro', updated_at: 1_700_000_100 },
  ];
});
afterEach(cleanup);

it('lists dub projects newest first and marks the one that is open', async () => {
  renderList();
  const open = await screen.findByRole('button', { name: /Podcast intro/ });
  expect(open).toHaveAttribute('aria-current', 'true');
  const names = screen.getAllByRole('button').map((b) => b.textContent ?? '');
  expect(names[0]).toContain('Podcast intro');
  expect(screen.getByRole('link')).toHaveAttribute('href', '/projects');
});

it('opens a dub project into the Dub workspace', async () => {
  renderList();
  await confirmProject(/Launch video/);
  await waitFor(() => expect(state.navigate).toHaveBeenCalledWith({ to: '/dub' }));
  expect(state.openDub).toHaveBeenCalled();
});

it('waits while dubbing work is running', async () => {
  state.dub = { project: null, phase: 'dubbing', recovery: null, instruct: '' };
  renderList();
  expect(await screen.findByRole('button', { name: /Launch video/ })).toBeDisabled();
});

it('shows the empty state when there are no dub projects yet', async () => {
  state.dubs = [];
  renderList();
  expect(await screen.findByText('projects.empty')).toBeVisible();
});

it('asks before replacing a draft and leaves it unchanged when cancelled', async () => {
  renderList();
  fireEvent.click(await screen.findByRole('button', { name: /Launch video/ }));
  expect(await screen.findByText('projectActions.open')).toBeVisible();
  expect(state.readProject).not.toHaveBeenCalled();
  expect(state.openDub).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  expect(screen.queryByText('projectActions.open')).toBeNull();
  expect(state.openDub).not.toHaveBeenCalled();
  expect(state.navigate).not.toHaveBeenCalled();
});

function pauseProjectRead() {
  let resolve!: (value: unknown) => void;
  state.readProject.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  return resolve;
}

it('waits for one project to open before accepting another selection', async () => {
  const resolve = pauseProjectRead();
  renderList();
  await confirmProject(/Launch video/);
  const other = screen.getByRole('button', { name: /Podcast intro/ });
  expect(other).toBeDisabled();
  fireEvent.click(other);
  await act(async () => resolve(state.dubs[0]));
  await waitFor(() => expect(state.navigate).toHaveBeenCalledTimes(1));
  expect(state.readProject).toHaveBeenCalledTimes(1);
  expect(state.openDub).toHaveBeenCalledWith(state.dubs[0]);
});

it('does not open or navigate after leaving the projects pane during a load', async () => {
  const resolve = pauseProjectRead();
  const view = renderList();
  await confirmProject(/Launch video/);
  view.unmount();
  await act(async () => resolve(state.dubs[0]));
  expect(state.openDub).not.toHaveBeenCalled();
  expect(state.navigate).not.toHaveBeenCalled();
});

it('cancels a load when its tab hides, even if the user returns before it finishes', async () => {
  const resolve = pauseProjectRead();
  const view = renderList();
  await confirmProject(/Launch video/);
  view.setActive(false);
  view.setActive(true);
  await act(async () => resolve(state.dubs[0]));
  expect(state.openDub).not.toHaveBeenCalled();
  expect(state.navigate).not.toHaveBeenCalled();
});

it('rechecks active work after a pending project read', async () => {
  const resolve = pauseProjectRead();
  renderList();
  await confirmProject(/Launch video/);
  state.longform = { ...state.longform, active: 'render' };
  await act(async () => resolve(state.dubs[0]));
  expect(await screen.findByRole('alert')).toHaveTextContent('sidebarTools.wait');
  expect(state.openDub).not.toHaveBeenCalled();
  expect(state.navigate).not.toHaveBeenCalled();
});

it('requires a fresh confirmation if the working draft changes during a load', async () => {
  const resolve = pauseProjectRead();
  renderList();
  await confirmProject(/Launch video/);
  state.dub = { ...state.dub, instruct: 'Keep these new directions' };
  await act(async () => resolve(state.dubs[0]));
  expect(state.openDub).not.toHaveBeenCalled();
  expect(state.navigate).not.toHaveBeenCalled();
  expect(await screen.findByRole('alert')).toHaveTextContent('projects.draft_changed');
  expect(screen.getByRole('button', { name: 'common.confirm' })).toBeEnabled();
  state.readProject.mockResolvedValueOnce(state.dubs[0]);
  fireEvent.click(screen.getByRole('button', { name: 'common.confirm' }));
  await waitFor(() => expect(state.navigate).toHaveBeenCalledWith({ to: '/dub' }));
});

it('localizes the error when dubbing work starts during a project load', async () => {
  state.openDub.mockReturnValue(false);
  renderList();
  await confirmProject(/Launch video/);
  expect(await screen.findByRole('alert')).toHaveTextContent('sidebarTools.wait');
  expect(state.navigate).not.toHaveBeenCalled();
});
