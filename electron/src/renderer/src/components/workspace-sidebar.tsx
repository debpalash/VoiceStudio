import { useState, type CSSProperties, type ReactNode } from 'react';
import { PanelLeftCloseIcon, PanelLeftOpenIcon, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { cn } from '@/lib/utils';

const WIDTHS = {
  default: { minimum: 248, initial: 280, maximum: 515, reserve: 480 },
  wide: { minimum: 296, initial: 344, maximum: 616, reserve: 520 },
  spacious: { minimum: 352, initial: 416, maximum: 750, reserve: 560 },
} as const;

const VARIANT_STYLES = {
  controls:
    '[&>section]:shadow-[inset_0_1px_0_color-mix(in_oklab,var(--foreground)_3%,transparent)] [&>details]:shadow-[inset_0_1px_0_color-mix(in_oklab,var(--foreground)_3%,transparent)] [&_summary]:min-h-8 [&_summary]:select-none [&_summary]:items-center [&_summary]:px-0.5 [&_summary]:font-medium [&_summary]:text-foreground/90 [&_summary:hover]:bg-background/35 [&_h2]:text-foreground/90 [&_h3]:text-foreground/90',
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
}: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
  className?: string;
  size?: 'default' | 'wide' | 'spacious';
  variant?: 'controls' | 'navigation' | 'library';
  meta?: ReactNode;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
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
        'secondary-sidebar relative [--pane-resize-display:flex] @max-[40rem]:[--pane-resize-display:none] flex min-h-0 shrink-0 flex-col border-r border-border/55 bg-[color-mix(in_oklab,var(--muted)_13%,var(--background))] shadow-[inset_-1px_0_0_color-mix(in_oklab,var(--foreground)_2%,transparent)] [container-type:inline-size]',
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
        data-slot="secondary-sidebar-header"
        className={cn(
          'workspace-titlebar flex h-12 shrink-0 items-center gap-2.5 border-b border-border/45 bg-background/30 shadow-[0_1px_0_color-mix(in_oklab,var(--foreground)_2%,transparent)] backdrop-blur-xl',
          collapsed ? 'justify-center px-1' : 'px-3',
        )}
      >
        {!collapsed && (
          <>
            <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-border/45 bg-background/50 text-primary shadow-[inset_0_1px_0_rgb(255_255_255/7%)]">
              <Icon aria-hidden="true" className="size-4" />
            </span>
            <span
              className="min-w-0 flex-1 truncate text-sm font-semibold tracking-[-0.014em] text-foreground/95"
              title={title}
            >
              {title}
            </span>
            {meta !== undefined && meta !== null && (
              <span className="shrink-0 rounded-md border border-border/40 bg-muted/55 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
                {meta}
              </span>
            )}
          </>
        )}
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={t(collapsed ? 'paneActions.expand' : 'paneActions.collapse')}
          title={t(collapsed ? 'paneActions.expand' : 'paneActions.collapse')}
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((value) => !value)}
          className="text-muted-foreground hover:bg-accent/60 hover:text-foreground"
        >
          {collapsed ? <PanelLeftOpenIcon /> : <PanelLeftCloseIcon />}
        </Button>
      </div>
      <div
        hidden={collapsed}
        data-slot="secondary-sidebar-content"
        className={cn(
          'studio-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain p-3.5 text-[13px] [scroll-padding-block:0.875rem] [scrollbar-gutter:stable] [&>*]:min-w-0 [&>section]:w-full [&>section]:shrink-0 [&_button]:max-w-full [&_h2]:tracking-[-0.012em] [&_h3]:tracking-[-0.01em] [&_input]:max-w-full [&_label]:leading-5 [&_p]:leading-[1.55] [&_summary]:rounded-lg [&_summary]:outline-none [&_summary]:transition-[color,background-color] [&_summary]:duration-150 [&_summary:hover]:text-foreground [&_summary:focus-visible]:ring-2 [&_summary:focus-visible]:ring-ring/35 [&_textarea]:max-w-full',
          VARIANT_STYLES[variant],
          collapsed && 'hidden',
          className,
        )}
      >
        {children}
      </div>
      {collapsed && (
        <button
          type="button"
          title={title}
          aria-label={title}
          onClick={() => setCollapsed(false)}
          className="group mx-auto my-2 flex min-h-0 flex-1 flex-col items-center gap-2 rounded-lg px-1.5 py-2 text-muted-foreground outline-none transition-[color,background-color,border-color] hover:bg-accent/65 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-md border border-border/45 bg-background/45 text-primary">
            <Icon aria-hidden="true" className="size-4" />
          </span>
          <span className="min-h-0 truncate text-[11px] font-semibold tracking-[0.01em] [writing-mode:vertical-rl] [text-orientation:mixed]">
            {title}
          </span>
        </button>
      )}
    </aside>
  );
}
