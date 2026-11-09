import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FilmIcon } from 'lucide-react';
import { MAC_TRAFFIC_LIGHTS_INLINE_END } from '@shared/utils/windowChrome';
import { SecondarySidebar } from './workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';

const platform = vi.hoisted(() => ({ mac: false, fullscreen: false }));
vi.mock('@/components/bridge', () => ({ isMac: () => platform.mac }));
vi.mock('@/hooks/use-window-fullscreen', () => ({ useWindowFullscreen: () => platform.fullscreen }));
vi.mock('@/components/app-shell/support-shortcut', () => ({ SupportShortcut: () => null }));
vi.mock('@/components/app-shell/sidebar-toggle', () => ({ SidebarToggle: () => null }));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const pane = (extra: Partial<React.ComponentProps<typeof SecondarySidebar>> = {}) => (
  <SecondarySidebar title="Studio" icon={FilmIcon} {...extra}>
    {extra.children ?? <p>content</p>}
  </SecondarySidebar>
);
const slot = (name: string) => document.querySelector<HTMLElement>(`[data-slot="${name}"]`);
const gridWide = () => {
  vi.stubGlobal('ResizeObserver', NoopObserver);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1800);
};

describe('width', () => {
  it('goes up to 750px for the spacious size and survives a remount', () => {
    gridWide();
    const first = render(pane({ size: 'spacious' }));
    const handle = screen.getByRole('separator');
    expect(handle.getAttribute('aria-valuemax')).toBe('750');
    const start = Number(handle.getAttribute('aria-valuenow'));
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    const stored = localStorage.getItem('voicestudio.secondary-sidebar.spacious');
    expect(stored).toBe(String(start + 40));
    first.unmount();
    render(pane({ size: 'spacious' }));
    expect(screen.getByRole('separator').getAttribute('aria-valuenow')).toBe(stored);
  });
});

describe('pinned footer', () => {
  it('lives beside the content, not inside it, and follows the collapsed state', () => {
    const seen: boolean[] = [];
    render(pane({ footer: <b>pinned</b>, onCollapsedChange: (c) => seen.push(c) }));
    expect(slot('secondary-sidebar-footer')?.textContent).toBe('pinned');
    expect(slot('secondary-sidebar-content')?.contains(slot('secondary-sidebar-footer'))).toBe(
      false,
    );
    fireEvent.click(screen.getByTitle('paneActions.collapse'));
    expect(slot('secondary-sidebar-footer')).toBeNull();
    fireEvent.click(slot('secondary-sidebar-expand')!);
    expect(slot('secondary-sidebar-footer')).not.toBeNull();
    expect(seen).toEqual([true, false]);
  });

  it('only reports a collapsed value that differs from the one it holds', () => {
    const onCollapsedChange = vi.fn();
    render(pane({ collapsed: true, onCollapsedChange }));
    fireEvent.click(slot('secondary-sidebar-expand')!);
    expect(onCollapsedChange.mock.calls).toEqual([[false]]);
  });

  it('is absent when there is no footer, or the library tab is showing', () => {
    const { rerender } = render(pane({}));
    expect(slot('secondary-sidebar-footer')).toBeNull();
    rerender(
      pane({
        footer: <b>pinned</b>,
        library: <p>lib</p>,
        libraryLabel: 'Saved',
        tabsInHeader: true,
      }),
    );
    expect(slot('secondary-sidebar-footer')).not.toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Saved' }));
    expect(slot('secondary-sidebar-footer')).toBeNull();
  });
});

describe('tabs', () => {
  const tabbed = (library: React.ComponentProps<typeof SecondarySidebar>['library']) =>
    pane({ tabsInHeader: true, libraryLabel: 'Saved', library });

  it('orders controls first and moves with the arrow keys, Home included', () => {
    render(tabbed(<input aria-label="saved-field" />));
    const [controls, saved] = screen.getAllByRole('tab');
    expect([controls.textContent, saved.textContent]).toEqual(['Studio', 'Saved']);
    controls.focus();
    fireEvent.keyDown(controls, { key: 'ArrowRight' });
    expect(saved).toHaveFocus();
    expect(saved.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(saved, { key: 'Home' });
    expect(controls).toHaveFocus();
  });

  it('flips the forward arrow in right-to-left text', () => {
    render(tabbed(<p>lib</p>));
    const list = screen.getByRole('tablist');
    const real = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation((el, pseudo) =>
      el === list ? ({ direction: 'rtl' } as CSSStyleDeclaration) : real(el, pseudo),
    );
    const [controls, saved] = screen.getAllByRole('tab');
    controls.focus();
    fireEvent.keyDown(controls, { key: 'ArrowLeft' });
    expect(saved).toHaveFocus();
  });

  it('does not lose typing in either tab, and tells a render callback when it is shown', () => {
    const flags: boolean[] = [];
    render(
      pane({
        tabsInHeader: true,
        libraryLabel: 'Saved',
        library: (shown) => {
          flags.push(shown);
          return <input aria-label="saved-field" />;
        },
        children: <input aria-label="controls-field" />,
      }),
    );
    fireEvent.change(screen.getByLabelText('controls-field'), { target: { value: 'abc' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Saved' }));
    const saved = screen.getByLabelText('saved-field');
    fireEvent.change(saved, { target: { value: 'xyz' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Studio' }));
    expect((screen.getByLabelText('controls-field') as HTMLInputElement).value).toBe('abc');
    fireEvent.click(screen.getByRole('tab', { name: 'Saved' }));
    expect(screen.getByLabelText('saved-field')).toBe(saved);
    expect((saved as HTMLInputElement).value).toBe('xyz');
    expect(flags[0]).toBe(true);
    expect(flags.includes(false)).toBe(true);
  });
});

describe('macOS traffic lights', () => {
  const header = () => slot('secondary-sidebar-header')!;
  const columnStartingAt = (left: number) => {
    platform.mac = true;
    vi.stubGlobal('ResizeObserver', NoopObserver);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ left } as DOMRect);
    render(pane());
  };
  afterEach(() => {
    platform.mac = false;
  });

  it('insets the header when the column begins under them', () => {
    columnStartingAt(64);
    expect(header().style.paddingInlineStart).toBe(`${MAC_TRAFFIC_LIGHTS_INLINE_END - 64 + 12}px`);
    expect(header().style.paddingTop).toBe('');
  });

  it('leaves the header alone far from them', () => {
    columnStartingAt(300);
    expect(header().style.paddingInlineStart).toBe('');
  });

  it('moves header tabs to their own row beside the lights so labels stay readable', () => {
    platform.mac = true;
    vi.stubGlobal('ResizeObserver', NoopObserver);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ left: 64 } as DOMRect);
    render(pane({ tabsInHeader: true, libraryLabel: 'Saved', library: <p>lib</p> }));
    const list = screen.getByRole('tablist');
    expect(header().contains(list)).toBe(false);
    expect(screen.getByRole('tab', { name: 'Saved' })).toHaveAttribute('title', 'Saved');
  });

  it('keeps keyboard focus on the selected tab when fullscreen moves the tabs', () => {
    platform.mac = true;
    vi.stubGlobal('ResizeObserver', NoopObserver);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ left: 64 } as DOMRect);
    const view = render(pane({ tabsInHeader: true, libraryLabel: 'Saved', library: <p>lib</p> }));
    const saved = screen.getByRole('tab', { name: 'Saved' });
    fireEvent.click(saved);
    saved.focus();
    expect(header().contains(screen.getByRole('tablist'))).toBe(false);
    platform.fullscreen = true;
    try {
      view.rerender(pane({ tabsInHeader: true, libraryLabel: 'Saved', library: <p>lib</p> }));
      expect(header().contains(screen.getByRole('tablist'))).toBe(true);
      expect(screen.getByRole('tab', { name: 'Saved' })).toHaveFocus();
    } finally {
      platform.fullscreen = false;
    }
  });

  it('keeps header tabs in the header far from the lights', () => {
    platform.mac = true;
    vi.stubGlobal('ResizeObserver', NoopObserver);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ left: 300 } as DOMRect);
    render(pane({ tabsInHeader: true, libraryLabel: 'Saved', library: <p>lib</p> }));
    expect(header().contains(screen.getByRole('tablist'))).toBe(true);
  });
});

it('collapses to one expand control, leaving the titlebar row empty', () => {
  render(
    <SecondarySidebar title="Voice" icon={FilmIcon} collapsed onCollapsedChange={vi.fn()}>
      <section>Body</section>
    </SecondarySidebar>,
  );
  const header = document.querySelector<HTMLElement>('[data-slot="secondary-sidebar-header"]')!;
  expect(header.querySelector('button')).toBeNull();
  expect(screen.getAllByRole('button', { expanded: false })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Voice' })).toHaveAttribute('aria-expanded', 'false');
});

it('leaves the page title top left and hides a same-named pane title visually', () => {
  const page = (pane: string) => (
    <main data-slot="workspace-content">
      <WorkspaceHeader>
        <h1>Dub</h1>
      </WorkspaceHeader>
      <SecondarySidebar title={pane} icon={FilmIcon}>
        <section>Preview</section>
      </SecondarySidebar>
    </main>
  );
  const view = render(page('Dub'));
  const title = () => document.querySelector('[data-slot="secondary-sidebar-title"]')!;
  expect(screen.getByRole('heading', { name: 'Dub', level: 1 })).not.toHaveClass('sr-only');
  expect(title()).toHaveClass('sr-only');
  view.rerender(page('History'));
  expect(title()).not.toHaveClass('sr-only');
});
