import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  navigate: vi.fn(),
  patch: vi.fn(),
  external: vi.fn(),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  apiPath: (path: string) => '/api' + path,
}));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: mocks.external } }),
}));
vi.mock('@/lib/store/clone-settings', () => ({ patchCloneSettings: mocks.patch }));
vi.mock('@/components/audio-preview-button', () => ({ AudioPreviewButton: () => null }));
import { CommunityView, communityKey } from './community-view';
const item = {
  id: 'voice/1',
  type: 'voice' as const,
  name: 'Shared voice',
  icon: '',
  language: 'English',
  use_case: '',
  facets: {},
  _source_repo: 'author/repo',
};
beforeEach(() => {
  mocks.api.mockImplementation(async (path: string) => {
    if (path.startsWith('/community/items?'))
      return { total: 1, offset: 0, limit: 100, items: [item] };
    if (path.includes('/use?')) return { profile_id: 'saved' };
    if (path === '/profiles')
      return [{ id: 'saved', name: item.name, ref_text: 'Sample transcript', language: 'French' }];
    if (path.startsWith('/community/submit-url?'))
      return { url: 'https://github.com/example/repo/issues/new' };
    throw new Error(path);
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function show() {
  const toggle = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CommunityView favorites={[]} toggleFavorite={toggle} />
    </QueryClientProvider>,
  );
  return toggle;
}
it('scopes favorites by source and materializes recorded voices into cloning', async () => {
  const toggle = show();
  await screen.findByRole('heading', { name: item.name });
  fireEvent.click(screen.getByRole('button', { name: 'gallery.favorites: Shared voice' }));
  expect(toggle).toHaveBeenCalledWith('community:author/repo:voice/1');
  expect(communityKey({ ...item, _source_repo: 'different/repo' })).not.toBe(communityKey(item));
  fireEvent.click(screen.getByRole('button', { name: 'gallery.use_voice' }));
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: '/clone' }));
  expect(mocks.api).toHaveBeenCalledWith('/community/items/voice%2F1/use?name=Shared+voice', {
    method: 'POST',
  });
  expect(mocks.patch).toHaveBeenCalledWith(
    expect.objectContaining({
      selectedProfileId: 'saved',
      refText: 'Sample transcript',
    }),
  );
  expect(mocks.patch.mock.calls.at(-1)?.[0]).not.toHaveProperty('language');
});
it('opens the submission form only after an explicit click', async () => {
  show();
  await screen.findByRole('heading', { name: item.name });
  expect(mocks.external).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'gallery.submit_preset' }));
  await waitFor(() =>
    expect(mocks.external).toHaveBeenCalledWith('https://github.com/example/repo/issues/new'),
  );
});
