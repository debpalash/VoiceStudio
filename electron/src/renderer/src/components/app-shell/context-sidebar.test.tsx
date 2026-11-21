import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

const route = vi.hoisted(() => ({ pathname: '/clone' }));
const workspace = vi.hoisted(() => ({ libraryOpen: false, libraryTab: 'voices' as const }));
const setWorkspace = vi.hoisted(() => vi.fn());
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (s: { location: typeof route }) => unknown }) =>
    select({ location: route }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/store/workspace', () => ({
  useWorkspace: () => workspace,
  setWorkspace,
}));
vi.mock('@/features/clone/voices-sidebar', () => ({
  VoicesSidebar: () => <div>Voice library</div>,
}));

import { ContextSidebar } from './context-sidebar';

beforeEach(() => {
  workspace.libraryOpen = false;
  workspace.libraryTab = 'voices';
  setWorkspace.mockClear();
});

it('reopens a collapsed library pane when a library shortcut opens it', () => {
  const view = render(<ContextSidebar />);
  expect(document.querySelector('[data-slot="secondary-sidebar"]')).toHaveAttribute(
    'data-collapsed',
    'true',
  );

  workspace.libraryOpen = true;
  view.rerender(<ContextSidebar />);
  expect(document.querySelector('[data-slot="secondary-sidebar"]')).not.toHaveAttribute(
    'data-collapsed',
  );
  expect(screen.getByText('Voice library')).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'paneActions.collapse' }));
  expect(setWorkspace).toHaveBeenCalledWith({ libraryOpen: false });
});
