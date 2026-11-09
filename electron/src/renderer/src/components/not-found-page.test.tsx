import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: any) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: any) => <header>{children}</header>,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { NotFoundPage } from './not-found-page';

it('names an unknown address instead of redirecting, with a way home', () => {
  render(<NotFoundPage />);
  expect(screen.getByRole('heading', { name: 'app.not_found_title' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'nav.home' })).toHaveAttribute('href', '/');
});
