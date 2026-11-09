import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MAC_TRAFFIC_LIGHTS_BAND } from '@shared/utils/windowChrome';

const host = vi.hoisted(() => ({
  mac: false,
  open: vi.fn(),
  state: { compact: false, compactViewport: false, forceExpanded: false, secondaryWorkspace: true },
}));

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: any) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Echo keys; when a version is interpolated, append it so the label is checkable.
    t: (key: string, vars?: Record<string, string>) =>
      vars?.version ? `${key}@${vars.version}` : key,
  }),
}));
vi.mock('@/components/bridge', () => ({ isMac: () => host.mac, appVersion: () => '7.1.0' }));
vi.mock('@/hooks/use-window-fullscreen', () => ({ useWindowFullscreen: () => false }));
vi.mock('@/hooks/use-pane-resize', () => ({
  usePaneResize: () => ({ host: { current: null }, width: 220, separatorProps: {} }),
}));
vi.mock('@/lib/store/workspace', () => ({
  useWorkspace: () => ({ libraryOpen: true }),
  setWorkspace: vi.fn(),
}));
vi.mock('./use-workspace-sidebar', () => ({
  useWorkspaceSidebarState: () => ({ ...host.state, setOpen: host.open }),
}));
vi.mock('./workspace-menu', () => ({ WorkspaceNavigation: () => <nav aria-label="nav-stub" /> }));
vi.mock('./status-bar', () => ({
  StatusBar: ({ footerLeading }: { footerLeading?: unknown }) => (
    <footer aria-label="status-stub">{footerLeading as never}</footer>
  ),
}));
import { WorkspaceSidebar, versionTranslationKey } from './workspace-sidebar';

function show({ mac = false, compact = false } = {}) {
  host.mac = mac;
  host.state = { ...host.state, compact };
  return render(<WorkspaceSidebar />);
}
afterEach(() => {
  cleanup();
  host.open.mockClear();
});

describe('expanded sidebar', () => {
  it('stacks navigation above status and carries no library', () => {
    show();
    const aside = document.querySelector('aside[aria-label="app.name"]')!;
    const order = [...aside.querySelectorAll('[aria-label]')]
      .map((node) => node.getAttribute('aria-label'))
      .filter((label) => label === 'nav-stub' || label === 'status-stub');
    expect(order).toEqual(['nav-stub', 'status-stub']);
    expect(aside.querySelector('[data-slot="library"]')).toBeNull();
  });

  it('names the app with its version on the brand link', () => {
    show();
    expect(versionTranslationKey(true)).toBe('app.version_dev');
    expect(versionTranslationKey(false)).toBe('app.version');
    const link = screen.getByRole('link', { name: /app\.name app\.version/ });
    expect(link.getAttribute('href')).toBe('/');
    expect(link.getAttribute('aria-label')).toContain('@7.1.0');
  });

  it('closes through the header control', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'common.close' }));
    expect(host.open).toHaveBeenCalledWith(false);
  });

  it('pads below the traffic lights only on macOS, and spells out Settings there', () => {
    const { unmount } = show({ mac: true });
    const row = document.querySelector<HTMLElement>('[data-slot="titlebar-row"]')!;
    expect(row.style.paddingTop).toBe(`${MAC_TRAFFIC_LIGHTS_BAND}px`);
    expect(within(screen.getByLabelText('status-stub')).getByText('nav.settings')).toBeTruthy();
    unmount();
    show();
    expect(
      document.querySelector<HTMLElement>('[data-slot="titlebar-row"]')!.style.paddingTop,
    ).toBe('');
  });
});

describe('icon rail', () => {
  it('opens on a double click only', () => {
    show({ compact: true });
    const rail = document.querySelector('[data-slot="compact-main-sidebar"]')!;
    expect(rail.getAttribute('aria-label')).toBe('clone.saved_profiles');
    fireEvent.click(rail);
    expect(host.open).not.toHaveBeenCalled();
    fireEvent.doubleClick(rail);
    expect(host.open).toHaveBeenCalledTimes(1);
    expect(host.open).toHaveBeenCalledWith(true);
  });

  it.each([false, true])('has a labelled expand toggle with the brand mark (mac=%s)', (mac) => {
    show({ mac, compact: true });
    const toggle = screen.getByRole('button', { name: 'clone.toggle_sidebar' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.querySelector('[data-slot="rail-brand"]')).toBeTruthy();
    expect(toggle.querySelectorAll('svg').length).toBe(1);
    fireEvent.click(toggle);
    expect(host.open).toHaveBeenCalledWith(true);
  });

  it('sizes the rail to the row above the bottom player, never the whole viewport', () => {
    // A viewport-tall rail ran under the now-playing dock and hid its last items.
    show({ compact: true });
    const rail = document.querySelector('[data-slot="compact-main-sidebar"]')!;
    expect(rail.classList.contains('h-full')).toBe(true);
    expect(rail.className).not.toMatch(/\bh-(?:dvh|screen|svh)\b/);
  });
});
