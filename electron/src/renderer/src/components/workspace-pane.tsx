import type { ReactNode } from 'react';
import { ChevronRightIcon, XIcon, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { isMac } from './bridge';
import { cn } from '@/lib/utils';
import { Button } from './ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';

export function WorkspacePane({
  title,
  children,
  onClose,
  collapsible = false,
  layout = 'inspector',
  icon: Icon,
  className,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  collapsible?: boolean;
  layout?: 'inspector' | 'editor';
  icon?: LucideIcon;
  /** Extra layout classes, e.g. a page that overlays the pane as a drawer when narrow. */
  className?: string;
}) {
  const { t } = useTranslation();
  const editor = layout === 'editor';
  const { host, width, separatorProps } = usePaneResize({
    storageKey: editor ? 'voicestudio.editor-width' : 'voicestudio.inspector-width',
    side: 'right',
    minimum: editor ? 380 : 304,
    initial: editor ? 540 : 400,
    maximum: editor ? 760 : 560,
    reserve: editor ? 520 : 320,
  });
  return (
    <aside
      ref={host}
      aria-label={title}
      data-slot="workspace-pane"
      data-layout={layout}
      style={{ width, maxWidth: 'calc(100% - 3rem)' }}
      className={cn(
        'workspace-pane relative flex h-full min-h-0 shrink-0 flex-col border-l border-border/55 bg-[color-mix(in_oklab,var(--muted)_13%,var(--background))] shadow-[inset_1px_0_0_color-mix(in_oklab,var(--foreground)_2%,transparent)]',
        className,
      )}
    >
      <div
        {...separatorProps}
        aria-label={title}
        className="group/resize absolute inset-y-0 -left-1 z-20 flex w-2 cursor-col-resize touch-none items-center justify-center outline-none"
      >
        <span className="h-10 w-px rounded-full bg-border/0 transition-[height,background-color,box-shadow] duration-150 group-hover/resize:h-16 group-hover/resize:bg-primary/45 group-hover/resize:shadow-[0_0_8px_var(--primary)] group-focus-visible/resize:h-16 group-focus-visible/resize:bg-primary" />
      </div>
      <header
        className={cn(
          'workspace-titlebar flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border/45 bg-background/30 px-3 shadow-[0_1px_0_color-mix(in_oklab,var(--foreground)_2%,transparent)] backdrop-blur-xl',
          !isMac() && 'native-controls-right',
        )}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          {Icon && (
            <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-border/45 bg-background/50 text-primary shadow-[inset_0_1px_0_rgb(255_255_255/7%)]">
              <Icon aria-hidden="true" className="size-4" />
            </span>
          )}
          <h2 className="truncate text-sm font-semibold tracking-[-0.014em] text-foreground/95">
            {title}
          </h2>
        </div>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={t(collapsible ? 'paneActions.collapse' : 'common.close')}
          title={t(collapsible ? 'paneActions.collapse' : 'common.close')}
          onClick={onClose}
          className="text-muted-foreground hover:bg-accent/60 hover:text-foreground"
        >
          {collapsible ? <ChevronRightIcon /> : <XIcon />}
        </Button>
      </header>
      <div
        data-slot="workspace-pane-content"
        className="studio-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 text-[13px] [container-type:inline-size] [scroll-padding-block:1rem] [scrollbar-gutter:stable] [&_h2]:tracking-[-0.012em] [&_h3]:tracking-[-0.01em] [&_label]:leading-5 [&_p]:leading-[1.55] [&_summary]:rounded-lg [&_summary]:outline-none [&_summary]:transition-colors [&_summary:hover]:bg-background/35 [&_summary:focus-visible]:ring-2 [&_summary:focus-visible]:ring-ring/35"
      >
        {children}
      </div>
    </aside>
  );
}
