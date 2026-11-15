import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { CommunityItem } from '@shared/api/community-types';

type Player = { sourceKey: string; onPlaying(): void; onPause(): void };
const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  navigate: vi.fn(),
  mounts: 0,
  live: null as Player | null,
  players: new Map<string, { token: object; props: Player }>(),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  apiPath: (path: string) => '/api' + path,
}));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/bridge', () => ({ getBridge: () => null }));
vi.mock('@/lib/store/clone-settings', () => ({ patchCloneSettings: vi.fn() }));
vi.mock('@/components/media-player', async () => {
  const { useEffect, useRef } = await import('react');
  return {
    audioSource: (src: string) => ({ src }),
    audioLoaders: [],
    MediaProvider: () => null,
    StudioMediaPlayer: (props: Player) => {
      const token = useRef({}).current;
      mocks.live = props;
      mocks.players.set(props.sourceKey, { token, props });
      useEffect(() => {
        mocks.mounts++;
        return () => {
          if (mocks.players.get(props.sourceKey)?.token === token)
            mocks.players.delete(props.sourceKey);
          mocks.live = null;
        };
      }, []);
      return null;
    },
  };
});
import { CommunityView, communityKey } from './community-view';

let viewport = 900;
const mediaListeners = new Set<() => void>();
const clients: QueryClient[] = [];
const voices = (count: number, offset = 0): CommunityItem[] =>
  Array.from({ length: count }, (_, i) => ({
    id: 'fixture/' + (offset + i),
    type: 'voice',
    name: 'Fixture voice ' + (offset + i),
    icon: '',
    language: 'English',
    use_case: '',
    facets: {},
    _source_repo: 'owned/fixture',
  }));

beforeEach(() => {
  viewport = 900;
  mocks.api.mockReset();
  mocks.mounts = 0;
  mocks.live = null;
  mocks.players.clear();
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() {
      return viewport >= (query.includes('80rem') ? 1280 : 768);
    },
    addEventListener: (_: string, fn: () => void) => mediaListeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => mediaListeners.delete(fn),
  }));
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(() => viewport - 48);
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.tagName === 'SECTION' ? 480 : 180;
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const scroller = this.closest('section');
    const top = this.getAttribute('role') === 'list' ? 120 - (scroller?.scrollTop || 0) : 0;
    return {
      x: 0,
      y: top,
      top,
      left: 0,
      bottom: top + this.offsetHeight,
      right: this.offsetWidth,
      width: this.offsetWidth,
      height: this.offsetHeight,
      toJSON() {},
    };
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  mediaListeners.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function show(count: number, total = count) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const items = voices(count);
  const pages = Array.from({ length: Math.ceil(count / 100) }, (_, i) => ({
    total,
    offset: i * 100,
    limit: 100,
    items: items.slice(i * 100, (i + 1) * 100),
  }));
  client.setQueryData(['community-gallery', '*', '*'], {
    pages,
    pageParams: pages.map((page) => page.offset),
  });
  function Harness() {
    const [favorites, setFavorites] = useState<string[]>([]);
    return (
      <QueryClientProvider client={client}>
        <CommunityView
          favorites={favorites}
          toggleFavorite={(id) =>
            setFavorites((current) =>
              current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
            )
          }
        />
      </QueryClientProvider>
    );
  }
  return render(<Harness />);
}
function resize(width: number) {
  act(() => {
    viewport = width;
    for (const notify of mediaListeners) notify();
    fireEvent.resize(window);
  });
}

it('keeps all 100 cards of a short catalogue', () => {
  const view = show(100);
  expect(view.container.querySelectorAll('article')).toHaveLength(100);
  expect(screen.getByRole('heading', { name: 'Fixture voice 99' })).toBeInTheDocument();
  expect(mocks.api).not.toHaveBeenCalled();
});

it('bounds a thousand cached cards, scrolls to real later items and retains focused actions', async () => {
  const view = show(1000);
  await screen.findByRole('heading', { name: 'Fixture voice 0' });
  expect(view.container.querySelectorAll('article').length).toBeGreaterThan(0);
  expect(view.container.querySelectorAll('article').length).toBeLessThan(60);
  expect(screen.queryByRole('heading', { name: 'Fixture voice 999' })).toBeNull();
  const favorite = screen.getByRole('button', { name: 'gallery.favorites: Fixture voice 0' });
  act(() => favorite.focus());
  const scroller = view.container.querySelector('section')!;
  fireEvent.scroll(scroller, { target: { scrollTop: 30000 } });
  await waitFor(() =>
    expect(
      [...view.container.querySelectorAll('[aria-posinset]')].some(
        (node) => Number(node.getAttribute('aria-posinset')) > 100,
      ),
    ).toBe(true),
  );
  expect(favorite).toHaveFocus();
  fireEvent.click(favorite);
  expect(favorite).toHaveAttribute('aria-pressed', 'true');
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Fixture voice 999' } });
  expect(screen.getByRole('heading', { name: 'Fixture voice 999' })).toBeInTheDocument();
  expect(view.container.querySelectorAll('article')).toHaveLength(1);
});

it('preserves a real active preview through scrolling and responsive columns', async () => {
  const view = show(1000);
  expect(view.container.querySelectorAll('article').length).toBeLessThan(60);
  const first = screen.getByRole('heading', { name: 'Fixture voice 0' }).closest('article')!;
  fireEvent.click(within(first).getByRole('button', { name: 'clone.preview_voice' }));
  await waitFor(() => expect(mocks.mounts).toBe(1));
  act(() => mocks.live!.onPlaying());
  const source = mocks.live!.sourceKey;
  const focused = screen.getByRole('button', { name: 'gallery.favorites: Fixture voice 1' });
  act(() => focused.focus());
  const scroller = view.container.querySelector('section')!;
  fireEvent.scroll(scroller, { target: { scrollTop: 30000 } });
  await waitFor(() =>
    expect(
      [...view.container.querySelectorAll('[aria-posinset]')].some(
        (node) => Number(node.getAttribute('aria-posinset')) > 100,
      ),
    ).toBe(true),
  );
  expect(mocks.mounts).toBe(1);
  expect(mocks.live?.sourceKey).toBe(source);
  expect(within(first).getByRole('button', { name: 'player.pause' })).toBeInTheDocument();
  for (const [width, columns] of [
    [1400, 3],
    [700, 1],
    [900, 2],
  ]) {
    resize(width);
    await waitFor(() =>
      expect(first.closest('[role="list"]')).toHaveStyle({
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
      }),
    );
    expect(mocks.mounts).toBe(1);
    expect(mocks.live?.sourceKey).toBe(source);
    expect(
      screen.getByRole('button', { name: 'gallery.favorites: Fixture voice 1' }),
    ).toHaveFocus();
  }
  act(() => mocks.live!.onPause());
  fireEvent.scroll(scroller, { target: { scrollTop: 60000 } });
  expect(mocks.mounts).toBe(1);
  expect(within(first).getByRole('button', { name: 'clone.preview_voice' })).toBeInTheDocument();
});

it('keeps a loading preview mounted when Load more crosses the virtual threshold', async () => {
  mocks.api.mockImplementation(async (path: string) => {
    expect(path).toContain('offset=100');
    return { total: 200, offset: 100, limit: 100, items: voices(100, 100) };
  });
  const view = show(100, 200);
  const first = screen.getByRole('heading', { name: 'Fixture voice 0' }).closest('article')!;
  fireEvent.click(within(first).getByRole('button', { name: 'clone.preview_voice' }));
  await waitFor(() => expect(mocks.mounts).toBe(1));
  fireEvent.click(screen.getByRole('button', { name: 'gallery.load_more' }));
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'gallery.load_more' })).toBeNull(),
  );
  expect(mocks.api).toHaveBeenCalledTimes(1);
  expect(mocks.mounts).toBe(1);
  expect(mocks.live?.sourceKey).toBe(communityKey(voices(1)[0]!));
  expect(view.container.querySelectorAll('article').length).toBeGreaterThan(0);
  expect(view.container.querySelectorAll('article').length).toBeLessThan(60);
});

it.each([false, true])(
  'keeps the current owner while a distant replacement loads and is cancelled (paused=%s)',
  async (paused) => {
    const view = show(1000);
    const first = screen.getByRole('heading', { name: 'Fixture voice 0' }).closest('article')!;
    const keyA = communityKey(voices(1)[0]!);
    fireEvent.click(within(first).getByRole('button', { name: 'clone.preview_voice' }));
    await waitFor(() => expect(mocks.players.has(keyA)).toBe(true));
    act(() => mocks.players.get(keyA)!.props.onPlaying());
    if (paused) act(() => mocks.players.get(keyA)!.props.onPause());
    const owner = mocks.players.get(keyA)!.token;
    fireEvent.scroll(view.container.querySelector('section')!, { target: { scrollTop: 30000 } });
    await waitFor(() =>
      expect(
        [...view.container.querySelectorAll('[aria-posinset]')].some(
          (node) => Number(node.getAttribute('aria-posinset')) > 100,
        ),
      ).toBe(true),
    );
    const next = [...view.container.querySelectorAll('article')].find(
      (card) => Number(card.querySelector('h2')?.textContent?.replace('Fixture voice ', '')) > 100,
    )!;
    const nextIndex = Number(next.querySelector('h2')!.textContent!.replace('Fixture voice ', ''));
    const keyB = communityKey(voices(1, nextIndex)[0]!);
    const button = within(next).getByRole('button', { name: 'clone.preview_voice' });
    act(() => button.focus());
    fireEvent.click(button);
    await waitFor(() => expect(mocks.players.has(keyB)).toBe(true));
    expect(mocks.players.get(keyA)?.token).toBe(owner);
    fireEvent.click(within(next).getByRole('button', { name: 'common.loading · common.cancel' }));
    expect(mocks.players.get(keyA)?.token).toBe(owner);
    expect(mocks.players.has(keyB)).toBe(false);
    fireEvent.click(within(next).getByRole('button', { name: 'clone.preview_voice' }));
    await waitFor(() => expect(mocks.players.has(keyB)).toBe(true));
    act(() => mocks.players.get(keyB)!.props.onPlaying());
    expect(mocks.players.has(keyA)).toBe(false);
    expect(view.container.querySelectorAll('article').length).toBeLessThan(60);
  },
);

it('keeps both media instances when their shared row splits during resize', async () => {
  const view = show(1000);
  const keyA = communityKey(voices(1, 1)[0]!),
    keyB = communityKey(voices(1)[0]!);
  const a = screen.getByRole('heading', { name: 'Fixture voice 1' }).closest('article')!;
  const b = screen.getByRole('heading', { name: 'Fixture voice 0' }).closest('article')!;
  fireEvent.click(within(a).getByRole('button', { name: 'clone.preview_voice' }));
  await waitFor(() => expect(mocks.players.has(keyA)).toBe(true));
  act(() => mocks.players.get(keyA)!.props.onPlaying());
  fireEvent.click(within(b).getByRole('button', { name: 'clone.preview_voice' }));
  await waitFor(() => expect(mocks.players.has(keyB)).toBe(true));
  const ownerA = mocks.players.get(keyA)!.token,
    ownerB = mocks.players.get(keyB)!.token;
  act(() => screen.getByRole('searchbox').focus());
  fireEvent.scroll(view.container.querySelector('section')!, { target: { scrollTop: 30000 } });
  for (const width of [700, 1400, 900]) {
    resize(width);
    expect(mocks.players.get(keyA)?.token).toBe(ownerA);
    expect(mocks.players.get(keyB)?.token).toBe(ownerB);
  }
});

it('retains both neighboring rows around a focused control after scrolling away', async () => {
  const view = show(1000);
  const first = screen.getByRole('heading', { name: 'Fixture voice 1' }).closest('article')!;
  act(() => within(first).getAllByRole('button').at(-1)!.focus());
  fireEvent.scroll(view.container.querySelector('section')!, { target: { scrollTop: 30000 } });
  await waitFor(() =>
    expect(
      [...view.container.querySelectorAll('[aria-posinset]')].some(
        (node) => Number(node.getAttribute('aria-posinset')) > 100,
      ),
    ).toBe(true),
  );
  expect(screen.getByRole('heading', { name: 'Fixture voice 2' })).toBeInTheDocument();
  const later = [...view.container.querySelectorAll('article')].find(
    (card) => Number(card.querySelector('h2')?.textContent?.replace('Fixture voice ', '')) > 100,
  )!;
  const index = Number(later.querySelector('h2')!.textContent!.replace('Fixture voice ', ''));
  act(() => within(later).getAllByRole('button')[0]!.focus());
  fireEvent.scroll(view.container.querySelector('section')!, { target: { scrollTop: 0 } });
  expect(
    screen.getByRole('heading', { name: 'Fixture voice ' + (index - (index % 2) - 1) }),
  ).toBeInTheDocument();
});

it('does not correct a stale scroll offset when pointer focus mounts a measured neighbor', async () => {
  const scrollTo = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
    configurable: true,
    value: scrollTo,
  });
  const view = show(1000);
  const scroller = view.container.querySelector('section')!;
  act(() => screen.getByRole('button', { name: 'gallery.favorites: Fixture voice 0' }).focus());
  fireEvent.scroll(scroller, { target: { scrollTop: 30000 } });
  const later = await waitFor(() => {
    const cards = [...view.container.querySelectorAll('[data-grid-item]')];
    const card = cards.find((node) => Number(node.getAttribute('data-grid-item')) > 100);
    expect(card).toBeTruthy();
    return card as HTMLElement;
  });
  // A real browser scrolls an overscan control into view before pointerdown,
  // then delivers its scroll event after focus. Newly retained neighbors may
  // differ from their estimate while the virtualizer still knows the old offset.
  scroller.scrollTop = 29141;
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.tagName === 'SECTION' ? 480 : 178;
  });
  scrollTo.mockClear();
  act(() => within(later).getAllByRole('button')[0]!.focus());
  expect(scrollTo).not.toHaveBeenCalled();
  expect(scroller.scrollTop).toBe(29141);
  fireEvent.scroll(scroller);
  await waitFor(() => expect(scrollTo).toHaveBeenCalled());
  fireEvent.click(within(later).getAllByRole('button')[0]!);
  expect(within(later).getAllByRole('button')[0]).toHaveAttribute('aria-pressed', 'true');
});
