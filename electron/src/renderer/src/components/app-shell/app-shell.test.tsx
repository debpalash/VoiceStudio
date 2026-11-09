import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const where = vi.hoisted(() => ({ path: '/settings/general', mac: false }));

vi.mock('@tanstack/react-router', () => ({
  Outlet: () => <i data-mock="outlet" />,
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { pathname: where.path } }),
}));
vi.mock('./workspace-sidebar', () => ({ WorkspaceSidebar: () => <i data-mock="sidebar" /> }));
vi.mock('./context-sidebar', () => ({ ContextSidebar: () => <i data-mock="context" /> }));
vi.mock('./account-corner', () => ({ AccountCorner: () => <i data-mock="corner" /> }));
vi.mock('./repair-agent-dock', () => ({ RepairAgentDock: () => null }));
vi.mock('@/components/command-palette', () => ({ CommandPalette: () => null }));
vi.mock('@/components/now-playing-dock', () => ({ NowPlayingDock: () => null }));
vi.mock('../backend-gate', () => ({
  BackendGate: ({ children }: { children: unknown }) => <>{children as never}</>,
}));
vi.mock('../bridge', () => ({ isMac: () => where.mac }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'running' }) }));
vi.mock('@shared/utils/backendStage', () => ({ isBackendReachable: () => true }));
import { AppShell } from './app-shell';

afterEach(cleanup);

const mounted = (path: string) => {
  where.path = path;
  const { container } = render(<AppShell />);
  const has = (name: string) => container.querySelectorAll(`[data-mock="${name}"]`).length;
  return { has };
};

describe('AppShell', () => {
  it('shows the sidebar and the page on Settings, without the context pane', () => {
    const page = mounted('/settings/appearance');
    expect([page.has('sidebar'), page.has('outlet'), page.has('context')]).toEqual([1, 1, 0]);
  });

  it('shows sidebar and context pane on a workspace route', () => {
    const page = mounted('/design');
    expect([page.has('sidebar'), page.has('context'), page.has('outlet')]).toEqual([1, 1, 1]);
  });

  it.each(['/design', '/settings/general'])('keeps the account corner on %s', (path) => {
    expect(mounted(path).has('corner')).toBe(1);
  });
});

it.each([
  ['/pro', true],
  ['/integrations', true],
  ['/settings/general', false],
])('gives %s headers clearance from the macOS account corner: %s', (path, padded) => {
  where.mac = true;
  where.path = path;
  const { container } = render(<AppShell />);
  expect(container.firstElementChild!.classList.contains('macos-notification-safe-area')).toBe(
    padded,
  );
  where.mac = false;
});
