import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const route = vi.hoisted(() => ({ pathname: '/clone' }));
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: any) => select({ location: route }),
}));
import { setWorkspace } from '@/lib/store/workspace';
import { useWorkspaceSidebarState } from './use-workspace-sidebar';

function viewport(wide: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: !wide,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

beforeEach(() => {
  viewport(true);
  route.pathname = '/clone';
  setWorkspace({ libraryOpen: true, expandedLibraryContext: null, autoCollapseSidebar: true });
});
afterEach(() => vi.unstubAllGlobals());

const state = () => renderHook(() => useWorkspaceSidebarState());

it('expands an auto-collapsed rail even though the library flag is already open', () => {
  const hook = state();
  expect(hook.result.current.compact).toBe(true);
  act(() => hook.result.current.setOpen(true));
  expect(hook.result.current.compact).toBe(false);
  expect(hook.result.current.forceExpanded).toBe(true);
  act(() => hook.result.current.setOpen(false));
  expect(hook.result.current.compact).toBe(true);
});

it('does not carry a forced expansion to another route', () => {
  const hook = state();
  act(() => hook.result.current.setOpen(true));
  expect(hook.result.current.compact).toBe(false);
  route.pathname = '/design';
  hook.rerender();
  expect(hook.result.current.forceExpanded).toBe(false);
  expect(hook.result.current.compact).toBe(true);
});

it.each(['/clone', '/', '/design', '/settings/appearance'])(
  'uses the icon rail beside the pane of %s on a wide window',
  (path) => {
    route.pathname = path;
    const hook = state();
    expect(hook.result.current.secondaryWorkspace).toBe(true);
    expect(hook.result.current.compactViewport).toBe(false);
    expect(hook.result.current.compact).toBe(true);
  },
);

it.each(['/integrations', '/integrations/n8n', '/pro', '/workflows', '/clone'])(
  'keeps one navigation layout on %s',
  (path) => {
    route.pathname = path;
    expect(state().result.current.compact).toBe(true);
  },
);

it('syncs an explicit collapse after the user expands the rail', () => {
  route.pathname = '/integrations';
  const hook = state();
  act(() => hook.result.current.setOpen(true));
  expect(hook.result.current.compact).toBe(false);
  act(() => hook.result.current.setOpen(false));
  expect(hook.result.current.compact).toBe(true);
});

it('shrinks any page in a narrow window while the preference is on', () => {
  viewport(false);
  route.pathname = '/integrations';
  expect(state().result.current.compact).toBe(true);
});

it('keeps the sidebar expanded beside section panes when the preference is off', () => {
  act(() => setWorkspace({ autoCollapseSidebar: false }));
  for (const path of ['/clone', '/design', '/settings/general']) {
    route.pathname = path;
    expect(state().result.current.compact).toBe(false);
  }
  viewport(false);
  expect(state().result.current.compact).toBe(false);
  const hook = state();
  act(() => hook.result.current.setOpen(false));
  expect(hook.result.current.compact).toBe(true);
});

it('records which route and viewport the expansion belongs to', () => {
  const getWorkspaceSnapshot = () =>
    JSON.parse(localStorage.getItem('voicestudio.workspace-layout')!);
  const hook = state();
  act(() => hook.result.current.setOpen(true));
  expect(getWorkspaceSnapshot()).toMatchObject({
    libraryOpen: true,
    expandedLibraryContext: '/clone:false',
  });
});
