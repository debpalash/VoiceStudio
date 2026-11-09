import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/bridge', () => ({ isMac: () => true }));
vi.mock('./support-shortcut', () => ({ SupportShortcut: () => null }));
vi.mock('./sidebar-toggle', () => ({ SidebarToggle: () => null }));

import { WorkspaceHeader } from './workspace-header';

afterEach(cleanup);

const header = () => document.querySelector('[data-slot="workspace-header"]')!;

it('always shows the page title, top left, with no pane-dependent hiding', () => {
  render(
    <WorkspaceHeader>
      <h1>Design</h1>
    </WorkspaceHeader>,
  );
  expect(screen.getByRole('heading', { name: 'Design', level: 1 })).toBeVisible();
  expect(header().firstElementChild?.className ?? '').not.toContain('sr-only');
});

it('keeps every header control on the 28px title bar row', () => {
  render(
    <WorkspaceHeader>
      <h1>Design</h1>
    </WorkspaceHeader>,
  );
  expect(screen.getByRole('button', { name: 'preferences.search' })).toHaveClass('h-7');
});

it('lets a page item pushed right sit beside search instead of mid-bar', () => {
  render(
    <WorkspaceHeader>
      <h1>Home</h1>
      <a href="#/projects" className="ms-auto">
        Projects
      </a>
    </WorkspaceHeader>,
  );
  // Page items share one growing row, so only one auto margin competes for the free space.
  expect(screen.getByRole('link', { name: 'Projects' }).parentElement).toHaveClass('flex-1');
  expect(screen.getByRole('button', { name: 'preferences.search' }).parentElement).not.toHaveClass(
    'ml-auto',
  );
});
