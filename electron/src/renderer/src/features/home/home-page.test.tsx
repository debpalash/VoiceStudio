import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
const browser = vi.hoisted(() => ({ open: vi.fn(async () => {}) }));
const files = vi.hoisted(() => ({ openExternal: vi.fn(async () => {}) }));
vi.mock('@/components/bridge', () => ({ getBridge: () => ({ browser, files }) }));

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useNavigate: () => vi.fn(),
}));
vi.mock('@tanstack/react-query', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-query')>()),
  useQuery: () => ({ data: [] }),
}));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }) }));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { HomePage } from './home-page';
import { homeContributors } from './home-contributors-data';
import en from '@/i18n/locales/en.json';

afterEach(cleanup);

it('keeps the heading and description together beside the contributor artwork', () => {
  render(<HomePage />);
  const heading = screen.getByRole('heading', { name: 'homeUi.title' });
  const description = screen.getByText('homeUi.subtitle');
  const copy = heading.parentElement!;
  expect(copy).toContainElement(description);
  expect(copy.querySelector('.home-contributors')).toBeNull();
  expect(copy.nextElementSibling).toHaveClass('home-contributors');
});

it('previews the official website only when the title is clicked', () => {
  browser.open.mockClear();
  render(<HomePage />);
  const title = screen.getByRole('link', { name: 'homeUi.title' });
  expect(en.homeUi.title).toBe('VoiceStudio.sh Open Source');
  expect(title).toHaveAttribute('href', 'https://voicestudio.sh');
  fireEvent.mouseEnter(title);
  expect(browser.open).not.toHaveBeenCalled();
  fireEvent.click(title);
  expect(browser.open).toHaveBeenCalledWith('https://voicestudio.sh');
});

it('names the feature Dubbing consistently while preserving the existing route', () => {
  expect(en.dubWorkspace.title).toBe('Dubbing');
  expect(en.nav.dub).toBe('Dubbing');
  expect(en.keyboard.dub).toBe('Dubbing');
  render(<HomePage />);
  expect(screen.getByRole('link', { name: /dubWorkspace.title/ })).toHaveAttribute('href', '/dub');
});

it('credits qualifying contributors and opens their GitHub profiles externally', () => {
  render(<HomePage />);
  const credits = screen.getAllByRole('listitem');
  expect(credits).toHaveLength(homeContributors.length + 1);
  expect(
    [1, 2, 3].map((row) => credits.filter((credit) => credit.style.gridRow === String(row)).length),
  ).toEqual([5, 3, 2]);
  expect(homeContributors.every((person) => person.commits > 10)).toBe(true);
  const all = screen.getByRole('link', { name: 'homeUi.allContributors' });
  expect(all).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio/graphs/contributors',
  );
  fireEvent.click(all);
  expect(files.openExternal).toHaveBeenCalledWith(
    'https://github.com/debpalash/VoiceStudio/graphs/contributors',
  );
  for (const { login } of homeContributors) {
    if (login === 'debpalash') continue;
    const profile = screen.getByRole('link', { name: `GitHub · @${login}` });
    expect(profile).toHaveAttribute('href', `https://github.com/${login}`);
    expect(profile.querySelector('img')?.src).not.toMatch(/^https?:\/\/(avatars|github)/);
    fireEvent.click(profile);
    expect(files.openExternal).toHaveBeenCalledWith(`https://github.com/${login}`);
  }
});

it('opens both creator profiles from the accessible avatar card', async () => {
  render(<HomePage />);
  fireEvent.click(screen.getByRole('button', { name: '@debpalash' }));
  const github = await screen.findByRole('link', { name: 'GitHub @debpalash' });
  const x = screen.getByRole('link', { name: 'X @idebpalash' });
  expect(github).toHaveAttribute('href', 'https://github.com/debpalash');
  expect(x).toHaveAttribute('href', 'https://x.com/idebpalash');
  fireEvent.click(github);
  expect(files.openExternal).toHaveBeenCalledWith('https://github.com/debpalash');
  fireEvent.click(x);
  expect(files.openExternal).toHaveBeenCalledWith('https://x.com/idebpalash');
});

it('separates three primary creation actions and describes all nine destinations', () => {
  render(<HomePage />);
  const clone = screen.getByRole('link', { name: /nav.clone/ });
  const primary = clone.parentElement!;
  expect(Array.from(primary.querySelectorAll('a'), (a) => a.getAttribute('href'))).toEqual([
    '/clone',
    '/design',
    '/dub',
  ]);
  expect(primary.nextElementSibling?.querySelectorAll('a')).toHaveLength(6);
  const icons = document.querySelectorAll('svg.home-feature-svg');
  expect(icons).toHaveLength(9);
  icons.forEach((icon) => expect(icon).toHaveAttribute('aria-hidden', 'true'));
  for (const key of ['design', 'dub', 'stories', 'audiobook', 'transcribe']) {
    expect(screen.getByText('homeUi.' + key)).toBeVisible();
  }
  expect(screen.queryByText('demo.dictation_lede')).not.toBeInTheDocument();
  expect(screen.queryByText('dub.supported_formats')).not.toBeInTheDocument();
  expect(screen.queryByText('clone.history_title')).not.toBeInTheDocument();
  expect(screen.queryByText('0')).not.toBeInTheDocument();
});
