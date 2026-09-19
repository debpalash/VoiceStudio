import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

// The left column must not change between workspaces and Settings: the main
// sidebar (navigation, library, status) is rendered on every route, so
// opening Settings never swaps it for a different panel.

const route = vi.hoisted(() => ({ pathname: '/clone' }));
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: any) => select({ location: route }),
  Outlet: () => <div data-testid="outlet" />,
}));
vi.mock('./workspace-sidebar', () => ({
  WorkspaceSidebar: () => <aside data-testid="main-sidebar" />,
}));
vi.mock('./sponsor-footer', () => ({ SponsorFooter: () => null }));
vi.mock('./repair-agent-dock', () => ({ RepairAgentDock: () => null }));
vi.mock('./system-notifications', () => ({ SystemNotifications: () => null }));
vi.mock('@/components/command-palette', () => ({ CommandPalette: () => null }));
vi.mock('../backend-gate', () => ({ BackendGate: ({ children }: any) => <>{children}</> }));
vi.mock('../bridge', () => ({ isMac: () => false }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
import { AppShell } from './app-shell';

it('keeps the main sidebar on Settings routes', () => {
  route.pathname = '/settings/models/tts';
  render(<AppShell />);
  expect(screen.getByTestId('main-sidebar')).toBeInTheDocument();
  expect(screen.getByTestId('outlet')).toBeInTheDocument();
});

it('renders the main sidebar on workspace routes as before', () => {
  route.pathname = '/clone';
  render(<AppShell />);
  expect(screen.getByTestId('main-sidebar')).toBeInTheDocument();
});
