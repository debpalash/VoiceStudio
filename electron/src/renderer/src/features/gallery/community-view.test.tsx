import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommunityItem } from '@shared/api/community-types';
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
import {
  CommunityView,
  authorProfileUrl,
  communityKey,
  isNewItem,
  matchesCommunitySearch,
  uniqueCommunityItems,
} from './community-view';
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
it('filters by pack on the server and keeps every pack as an option', async () => {
  mocks.api.mockImplementation(async (path: string) => {
    if (path.startsWith('/community/items?'))
      return {
        total: 1,
        offset: 0,
        limit: 100,
        items: [{ ...item, author: 'iluucasz', license: 'CC-BY-4.0' }],
        packs: [{ id: 'community-voices', name: 'Community Voices', count: 2 }],
        languages: ['English'],
      };
    throw new Error(path);
  });
  show();
  await screen.findByRole('heading', { name: item.name });
  fireEvent.click(screen.getByRole('button', { name: /Community Voices/ }));
  await waitFor(() =>
    expect(mocks.api).toHaveBeenCalledWith(
      expect.stringContaining('pack=community-voices'),
      expect.anything(),
    ),
  );
  // One language: no point offering a filter.
  expect(screen.queryByLabelText('gallery.community_language')).toBeNull();
  const author = screen.getByRole('link', { name: 'gallery.community_author_profile' });
  expect(author.getAttribute('href')).toBe('https://github.com/iluucasz');
  fireEvent.click(author);
  expect(mocks.external).toHaveBeenCalledWith('https://github.com/iluucasz');
});

it('marks items added in the last 30 days as new', () => {
  const now = Date.parse('2026-10-26T12:00:00Z');
  expect(isNewItem({ ...item, added_at: '2026-10-20' }, now)).toBe(true);
  expect(isNewItem({ ...item, added_at: '2026-08-01' }, now)).toBe(false);
  expect(isNewItem({ ...item, added_at: '2027-01-01' }, now)).toBe(false);
  expect(isNewItem(item, now)).toBe(false);
});

it('links only real GitHub handles, never the house author', () => {
  expect(authorProfileUrl('iluucasz')).toBe('https://github.com/iluucasz');
  expect(authorProfileUrl('VoiceStudio')).toBeNull();
  expect(authorProfileUrl('Jane Doe')).toBeNull();
  expect(authorProfileUrl('-bad')).toBeNull();
  expect(authorProfileUrl('a'.repeat(40))).toBeNull();
  expect(authorProfileUrl(undefined)).toBeNull();
  expect(authorProfileUrl(123)).toBeNull();
  expect(authorProfileUrl({ login: 'x' })).toBeNull();
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

describe('community catalogue hygiene', () => {
  const voice = (id: string, name: string, extra: Partial<CommunityItem> = {}): CommunityItem => ({
    id,
    type: 'preset',
    name,
    icon: '',
    use_case: '',
    facets: {},
    language: 'English',
    instruct: 'warm narrator',
    ...extra,
  });

  it('lists a voice once even when two sources or pages carry it', () => {
    const items = uniqueCommunityItems([
      voice('a', 'Ava'),
      voice('a', 'Ava'),
      voice('b', 'Ava', { _source_repo: 'mirror' }),
      voice('c', 'Ben', { instruct: 'bright' }),
    ]);
    expect(items.map((item) => item.id)).toEqual(['a', 'c']);
  });

  it('searches names, descriptions, authors and languages like the Archetypes list', () => {
    const item = voice('a', 'Ava', { author: 'maria', language: 'Spanish' });
    expect(matchesCommunitySearch(item, 'ava')).toBe(true);
    expect(matchesCommunitySearch(item, 'NARRATOR')).toBe(true);
    expect(matchesCommunitySearch(item, 'maria')).toBe(true);
    expect(matchesCommunitySearch(item, 'spanish')).toBe(true);
    expect(matchesCommunitySearch(item, 'robot')).toBe(false);
  });
});
