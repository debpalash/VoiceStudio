import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const route = vi.hoisted(() => ({ pathname: '/gallery' }));
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: any) => select({ location: route }),
  Link: ({ to, activeProps, children, ...props }: any) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/store/workspace', () => ({ setWorkspace: vi.fn() }));
import { WorkspaceNavigation } from './workspace-menu';

it('opens the current workflow, lets users collapse it, and follows route changes', () => {
  const { rerender } = render(<WorkspaceNavigation />);
  const voice = screen.getByRole('button', { name: 'nav.voice' });
  expect(voice).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: 'nav.gallery' })).toHaveAttribute('href', '/gallery');
  expect(screen.getByRole('link', { name: 'nav.calls' })).toHaveAttribute('href', '/calls');
  fireEvent.click(voice);
  expect(voice).toHaveAttribute('aria-expanded', 'false');
  route.pathname = '/audiobook';
  rerender(<WorkspaceNavigation />);
  expect(screen.getByRole('button', { name: 'nav.stories' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  expect(screen.getByRole('link', { name: 'audiobook.title' })).toHaveAttribute(
    'href',
    '/audiobook',
  );
});

it('keeps grouped destinations reachable from the compact rail', async () => {
  render(<WorkspaceNavigation compact />);
  fireEvent.click(screen.getByRole('button', { name: 'nav.voice' }));
  expect(await screen.findByRole('link', { name: 'nav.clone_short' })).toHaveAttribute(
    'href',
    '/clone',
  );
  expect(screen.getByRole('link', { name: 'nav.gallery' })).toHaveAttribute('href', '/gallery');
});
