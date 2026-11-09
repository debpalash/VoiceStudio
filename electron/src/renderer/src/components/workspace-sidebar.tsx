import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from 'react';
import { PanelLeftCloseIcon, PanelLeftOpenIcon, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { cn } from '@/lib/utils';
import { isMac } from '@/components/bridge';
import { useWindowFullscreen } from '@/hooks/use-window-fullscreen';

/**
 * How far a titlebar row must inset its content to clear the macOS traffic
 * lights. Measured, not assumed: the lights reach past the slim rail into a
 * column beside it, but never into one further right.
 */
function useTrafficLightInset(enabled: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [inset, setInset] = useState(0);
  useLayoutEffect(() => {
    const row = ref.current;
    if (!enabled || !row) return setInset(0);
    const measure = () =>
      setInset(
        Math.max(0, Math.ceil(MAC_TRAFFIC_LIGHTS_INLINE_END - row.getBoundingClientRect().left)),
      );
    measure();
    // The row moves when a column before it resizes (rail collapse, pane drag).
    const observer = new ResizeObserver(measure);
    const column = row.parentElement;
    for (let node = column?.previousElementSibling; node; node = node.previousElementSibling) {
      observer.observe(node);
    }
    observer.observe(row);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [enabled]);
  return [ref, inset] as const;
}
import { MAC_TRAFFIC_LIGHTS_INLINE_END } from '@shared/utils/windowChrome';
import { paneTitleInShell } from '@/components/app-shell/workspace-header';

/** Whether the page header above already shows this pane's title. */
function useTitleInShell(row: RefObject<HTMLElement | null>, title: string): boolean {
  const [inShell, setInShell] = useState(false);
  useLayoutEffect(() => {
    const node = row.current;
    if (!node) return;
    const check = () => setInShell(paneTitleInShell(node, title));
    check();
    const heading = node
      .closest('[data-slot="workspace-content"]')
      ?.querySelector('[data-slot="workspace-header"]');
    if (!heading || typeof MutationObserver === 'undefined') return;
    // Only the header is watched (its title changes with language), never the page.
    const observer = new MutationObserver(check);
    observer.observe(heading, { subtree: true, childList: true, characterData: true });
    return () => observer.disconnect();
  }, [row, title]);
  return inShell;
}

const WIDTHS = {
  default: { minimum: 248, initial: 280, maximum: 515, reserve: 480 },
  wide: { minimum: 296, initial: 344, maximum: 616, reserve: 520 },
  spacious: { minimum: 352, initial: 416, maximum: 750, reserve: 560 },
} as const;

const VARIANT_STYLES = {
  controls:
    '[&_summary]:min-h-8 [&_summary]:select-none [&_summary]:items-center [&_summary]:px-0.5 [&_summary]:font-medium [&_summary]:text-foreground/90 [&_summary:hover]:bg-background/35 [&_h2]:text-foreground/90 [&_h3]:text-foreground/90',
  navigation:
    '[&>button]:h-9 [&>button]:w-full [&>button]:justify-start [&>button]:gap-2.5 [&>button]:rounded-lg [&>button]:px-2.5 [&>button]:font-normal [&>button]:transition-[color,background-color,box-shadow] [&>button:hover]:bg-accent/55 [&>button[aria-pressed=true]]:bg-accent/80 [&>button[aria-pressed=true]]:font-medium [&>button[aria-pressed=true]]:shadow-[inset_2px_0_0_var(--primary)] [&>button>svg]:text-muted-foreground [&>button[aria-pressed=true]>svg]:text-primary',
  library:
    '[&_input]:bg-background/45 [&_input]:shadow-xs [&_button]:outline-none [&_button:focus-visible]:ring-2 [&_button:focus-visible]:ring-ring/40',
} as const;

/** Local workspace controls; the global library and navigation remain in the app shell. */
export function SecondarySidebar({
  title,
  icon: Icon,
  children,
  className,
  size = 'default',
  variant = 'controls',
  meta,
  footer,
  library,
  libraryLabel,
  collapsed: controlledCollapsed,
  onCollapsedChange,
  tabsInHeader = false,
}: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
  className?: string;
  size?: 'default' | 'wide' | 'spacious';
  variant?: 'controls' | 'navigation' | 'library';
  meta?: ReactNode;
  /** Pinned below the scrolling content; hidden while collapsed or on the library tab. */
  footer?: ReactNode;
  /** Saved items beside the controls. A render callback receives visibility so retained panes can cancel hidden work. */
  library?: ReactNode | ((active: boolean) => ReactNode);
  libraryLabel?: string;
  /** Controls the collapsed state when the parent owns a shared pane shortcut. */
  collapsed?: boolean;
  /** Reports each collapse or expand, so a page can re-home the footer while the pane is shut. */
  onCollapsedChange?: (collapsed: boolean) => void;
  /**
   * Show the controls/library tabs in the header row instead of the title, for
   * pages whose title bar and tabs already name the workspace.
   */
  tabsInHeader?: boolean;
}) {
  const { t } = useTranslation();
  const fullscreen = useWindowFullscreen();
  const [uncontrolledCollapsed, setUncontrolledCollapsed] = useState(false);
  const collapsed = controlledCollapsed ?? uncontrolledCollapsed;
  // Collapsed, the header row is an empty drag strip, so nothing needs to clear the lights.
  const [headerRef, lightsInset] = useTrafficLightInset(isMac() && !fullscreen && !collapsed);
  const titleInShell = useTitleInShell(headerRef, title);
  const [view, setView] = useState<'controls' | 'library'>('controls');
  const [libraryVisited, setLibraryVisited] = useState(false);
  const tabsId = useId();
  const chooseView = (next: 'controls' | 'library') => {
    if (next === 'library') setLibraryVisited(true);
    setView(next);
  };
  const showLibrary = library != null && view === 'library';
  const setCollapsed = (next: boolean | ((current: boolean) => boolean)) => {
    const resolved = typeof next === 'function' ? next(collapsed) : next;
    if (resolved === collapsed) return;
    if (controlledCollapsed === undefined) setUncontrolledCollapsed(resolved);
    // Called from the event handler, not a state updater, so StrictMode cannot fire it twice.
    onCollapsedChange?.(resolved);
  };
  // macOS traffic lights take ~90px of the header; two tabs beside them and the
  // collapse button truncate to "D…" / "S…", so they move to their own row there.
  const headerTabs = tabsInHeader && library != null && !lightsInset;
  // Entering or leaving fullscreen moves the tabs between rows, which remounts
  // them: keep keyboard focus on the selected tab when one had it.
  const lastHeaderTabs = useRef(headerTabs);
  const refocusTab = useRef(false);
  if (lastHeaderTabs.current !== headerTabs) {
    lastHeaderTabs.current = headerTabs;
    const active = typeof document === 'undefined' ? null : document.activeElement;
    refocusTab.current = !!active?.id && active.id.startsWith(`${tabsId}-`);
  }
  useLayoutEffect(() => {
    if (!refocusTab.current) return;
    refocusTab.current = false;
    document.getElementById(`${tabsId}-${view}`)?.focus();
  }, [headerTabs, tabsId, view]);
  const tabList = (className: string) => (
    <div
      role="tablist"
      aria-label={title}
      data-slot="secondary-sidebar-tabs"
      className={cn('flex min-w-0 shrink-0 gap-1', className)}
      onKeyDown={(event) => {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
        const current = tabs.findIndex((tab) => tab === event.target);
        if (current < 0) return;
        const forward =
          getComputedStyle(event.currentTarget).direction === 'rtl' ? 'ArrowLeft' : 'ArrowRight';
        const backward = forward === 'ArrowRight' ? 'ArrowLeft' : 'ArrowRight';
        const next =
          event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? 1
              : event.key === forward || event.key === backward
                ? 1 - current
                : null;
        if (next === null) return;
        event.preventDefault();
        chooseView(next === 0 ? 'controls' : 'library');
        tabs[next]?.focus();
      }}
    >
      {(
        [
          ['controls', title],
          ['library', libraryLabel ?? title],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          type="button"
          role="tab"
          id={`${tabsId}-${value}`}
          aria-controls={`${tabsId}-panel-${value}`}
          aria-selected={view === value}
          tabIndex={view === value ? 0 : -1}
          onClick={() => chooseView(value)}
          title={label}
          className={cn(
            'h-7 min-w-0 flex-1 truncate rounded-md px-2 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
            view === value
              ? 'bg-accent/80 text-foreground'
              : 'text-muted-foreground hover:bg-accent/45 hover:text-foreground',
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
  const dimensions = WIDTHS[size];
  const resize = usePaneResize({
    storageKey: `voicestudio.secondary-sidebar.${size}`,
    side: 'left',
    ...dimensions,
    enabled: !collapsed,
  });
  return (
    <aside
      ref={resize.host}
      aria-label={title}
      data-slot="secondary-sidebar"
      data-size={size}
      data-variant={variant}
      data-collapsed={collapsed || undefined}
      style={
        {
          '--secondary-sidebar-width': `${resize.width}px`,
        } as CSSProperties
      }
      className={cn(
        'secondary-sidebar relative [--pane-resize-display:flex] @max-[40rem]:[--pane-resize-display:none] flex min-h-0 shrink-0 flex-col border-r border-border/55 bg-[color-mix(in_oklab,var(--muted)_13%,var(--background))] [container-type:inline-size]',
        collapsed
          ? 'w-11'
          : 'w-[var(--secondary-sidebar-width)] @max-[40rem]:max-h-[40%] @max-[40rem]:w-full @max-[40rem]:border-r-0 @max-[40rem]:border-b',
      )}
    >
      {!collapsed && (
        <div
          {...resize.separatorProps}
          aria-label={title}
          className="group/resize absolute inset-y-0 -right-1 z-20 [display:var(--pane-resize-display)] w-2 cursor-col-resize touch-none items-center justify-center outline-none"
        >
          <span className="h-10 w-px rounded-full bg-border/0 transition-[height,background-color,box-shadow] duration-150 group-hover/resize:h-16 group-hover/resize:bg-primary/45 group-hover/resize:shadow-[0_0_8px_var(--primary)] group-focus-visible/resize:h-16 group-focus-visible/resize:bg-primary" />
        </div>
      )}
      <div
        ref={headerRef}
        data-slot="secondary-sidebar-header"
        style={lightsInset ? { paddingInlineStart: lightsInset + 12 } : undefined}
        className={cn(
          'workspace-titlebar flex h-12 shrink-0 items-center gap-2.5 border-b border-border/45',
          collapsed ? 'px-1' : 'px-3',
        )}
      >
        {!collapsed && headerTabs && tabList('flex-1')}
        {!collapsed && !headerTabs && (
          <>
            <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-border/45 bg-background/50 text-primary shadow-[inset_0_1px_0_rgb(255_255_255/7%)]">
              <Icon aria-hidden="true" className="size-4" />
            </span>
            {/* The page header already names the page top left; a same-named pane keeps
                its name for assistive tech only. */}
            <span
              data-slot="secondary-sidebar-title"
              className={cn(
                'min-w-0 flex-1 truncate text-sm font-semibold tracking-[-0.014em] text-foreground/95',
                titleInShell && 'sr-only',
              )}
              title={title}
            >
              {title}
            </span>
            {titleInShell && <span className="flex-1" aria-hidden="true" />}
            {meta !== undefined && meta !== null && (
              <span className="shrink-0 rounded-md border border-border/40 bg-muted/55 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
                {meta}
              </span>
            )}
          </>
        )}
        {!collapsed && (
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={t('paneActions.collapse')}
            title={t('paneActions.collapse')}
            aria-expanded
            onClick={() => setCollapsed(true)}
            className="text-muted-foreground hover:bg-accent/60 hover:text-foreground"
          >
            <PanelLeftCloseIcon />
          </Button>
        )}
      </div>
      {library != null &&
        !collapsed &&
        !headerTabs &&
        tabList('border-b border-border/45 px-2.5 py-2')}
      {library != null && (
        <div
          role="tabpanel"
          id={`${tabsId}-panel-library`}
          aria-labelledby={`${tabsId}-library`}
          hidden={!showLibrary || collapsed}
          className={cn('flex min-h-0 flex-1 flex-col', (!showLibrary || collapsed) && 'hidden')}
        >
          {libraryVisited
            ? typeof library === 'function'
              ? library(showLibrary && !collapsed)
              : library
            : null}
        </div>
      )}
      <div
        hidden={collapsed || showLibrary}
        role={library != null ? 'tabpanel' : undefined}
        id={library != null ? `${tabsId}-panel-controls` : undefined}
        aria-labelledby={library != null ? `${tabsId}-controls` : undefined}
        data-slot="secondary-sidebar-content"
        className={cn(
          'studio-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain p-3.5 text-[13px] [scroll-padding-block:0.875rem] [scrollbar-gutter:stable] [&>*]:min-w-0 [&>section]:w-full [&>section]:shrink-0 [&_button]:max-w-full [&_h2]:tracking-[-0.012em] [&_h3]:tracking-[-0.01em] [&_input]:max-w-full [&_label]:leading-5 [&_p]:leading-[1.55] [&_summary]:rounded-lg [&_summary]:outline-none [&_summary]:transition-[color,background-color] [&_summary]:duration-150 [&_summary:hover]:text-foreground [&_summary:focus-visible]:ring-2 [&_summary:focus-visible]:ring-ring/35 [&_textarea]:max-w-full',
          VARIANT_STYLES[variant],
          (collapsed || showLibrary) && 'hidden',
          className,
        )}
      >
        {children}
      </div>
      {footer != null && !collapsed && !showLibrary && (
        <div
          data-slot="secondary-sidebar-footer"
          // shrink-[0.05]: when the pane is height-capped (stacked layout) the scrolling content gives
          // way first and the footer only shrinks once that is exhausted.
          className="studio-scrollbar flex max-h-[70%] min-h-0 shrink-[0.05] flex-col gap-2 overflow-y-auto overscroll-contain border-t border-border/45 p-3"
        >
          {footer}
        </div>
      )}
      {collapsed && (
        <button
          type="button"
          title={`${t('paneActions.expand')} · ${title}`}
          aria-label={title}
          aria-expanded={false}
          data-slot="secondary-sidebar-expand"
          onClick={() => setCollapsed(false)}
          className="group mx-1 my-2 flex min-h-0 flex-1 flex-col items-center gap-2.5 rounded-lg px-1 py-2 text-muted-foreground outline-none transition-[color,background-color,border-color] hover:bg-accent/65 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-md border border-border/45 bg-background/45 text-primary">
            <Icon aria-hidden="true" className="size-4" />
          </span>
          <span className="min-h-0 truncate text-[11px] font-semibold tracking-[0.01em] [writing-mode:vertical-rl] [text-orientation:mixed]">
            {title}
          </span>
          <PanelLeftOpenIcon
            aria-hidden="true"
            className="mt-auto size-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
          />
        </button>
      )}
    </aside>
  );
}
