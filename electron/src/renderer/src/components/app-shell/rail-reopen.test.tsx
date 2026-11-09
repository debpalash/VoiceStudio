import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Real hook and store here; only the router, bridge and heavy children are replaced.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: any) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
  useRouterState: ({ select }: any) => select({ location: { pathname: '/clone' } }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/bridge', () => ({ isMac: () => false, appVersion: () => '1.0.0' }));
vi.mock('@/hooks/use-window-fullscreen', () => ({ useWindowFullscreen: () => false }));
vi.mock('@/hooks/use-pane-resize', () => ({
  usePaneResize: () => ({ host: { current: null }, width: 200, separatorProps: {} }),
}));
vi.mock('./workspace-menu', () => ({ WorkspaceNavigation: () => null }));
vi.mock('./status-bar', () => ({ StatusBar: () => null }));
import { setWorkspace } from '@/lib/store/workspace';
import { WorkspaceSidebar } from './workspace-sidebar';

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  setWorkspace({ libraryOpen: true, expandedLibraryContext: null, autoCollapseSidebar: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('promotes the clone rail into an open library pane', () => {
  render(<WorkspaceSidebar />);
  const compactToggle = screen.getByRole('button', { name: 'clone.toggle_sidebar' });
  fireEvent.click(compactToggle);

  const persisted = JSON.parse(localStorage.getItem('voicestudio.workspace-layout') || '{}');
  expect(persisted).toMatchObject({
    libraryOpen: true,
    expandedLibraryContext: '/clone:false',
  });
  expect(screen.getByRole('button', { name: 'common.close' })).toBeVisible();
  expect(compactToggle).not.toBeInTheDocument();
});
