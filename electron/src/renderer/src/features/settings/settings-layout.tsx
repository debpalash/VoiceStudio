// Adapted from T3 Code's settingsLayout and WorkspacePageContainer (MIT).
// See electron/T3CODE-LICENSE.txt.
import { createContext, useContext, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * The current Settings page title (the breadcrumb). A section with the same
 * title keeps its heading for screen readers only, so the page never repeats
 * its name as a small heading right under the breadcrumb.
 */
export const SettingsPageTitleContext = createContext<string | null>(null);

export function SettingsContent({
  children,
  fullBleed = false,
}: {
  children: ReactNode;
  fullBleed?: boolean;
}) {
  return (
    <div
      className={cn(
        '@container min-h-0 min-w-0 flex-1',
        fullBleed ? 'overflow-hidden' : 'overflow-y-auto [scrollbar-gutter:stable]',
      )}
    >
      <div
        className={cn(
          'flex w-full min-w-0 flex-col',
          fullBleed ? 'h-full min-h-0' : 'gap-6 px-4 pt-6 pb-12 @xl:px-6',
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function SettingsSection({
  title,
  children,
  icon: Icon,
  contentVariant = 'rows',
}: {
  title: string;
  children: ReactNode;
  icon?: LucideIcon;
  contentVariant?: 'rows' | 'cards';
}) {
  const repeatsPageTitle = useContext(SettingsPageTitleContext) === title;
  return (
    <section className="min-w-0 space-y-2.5">
      <h2
        className={
          repeatsPageTitle
            ? 'sr-only'
            : 'flex min-h-7 items-center gap-2 px-1 text-sm font-normal tracking-[-0.005em] text-foreground/80'
        }
      >
        {Icon && <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />}
        {title}
      </h2>
      <div
        className={cn(
          contentVariant === 'rows'
            ? 'glass-panel rounded-xl border border-border/60 bg-card/40 shadow-xs/5 [&>*+*]:border-t [&>*+*]:border-border/50'
            : 'grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-3',
        )}
      >
        {children}
      </div>
    </section>
  );
}

export function SettingsRow({
  className,
  id,
  title,
  titleHidden = false,
  description,
  children,
  variant = 'row',
  active = false,
}: {
  className?: string;
  id: string;
  title: ReactNode;
  titleHidden?: boolean;
  description?: ReactNode;
  children: ReactNode;
  variant?: 'row' | 'card';
  active?: boolean;
}) {
  return (
    <div
      data-slot="settings-row"
      data-active={active || undefined}
      className={cn(
        'px-4 py-3',
        variant === 'card' &&
          'glass-panel min-h-40 rounded-xl border border-border/60 bg-card/40 shadow-xs/5 transition-[border-color,background-color,box-shadow] duration-200 hover:border-border data-[active]:border-primary/35 data-[active]:bg-primary/[0.055] data-[active]:shadow-[0_0_0_1px_color-mix(in_oklab,var(--primary)_12%,transparent),0_12px_30px_-24px_var(--primary)] motion-reduce:transition-none',
        className,
      )}
    >
      <div
        className={cn(
          'flex min-w-0 flex-col items-stretch gap-3',
          variant === 'row'
            ? '@2xl:flex-row @2xl:items-center @2xl:justify-between @2xl:gap-x-8'
            : 'h-full',
        )}
      >
        <div className="min-w-0 flex-1 space-y-1">
          <h3
            tabIndex={-1}
            id={id}
            className={titleHidden ? 'sr-only' : 'min-h-5 text-sm font-medium tracking-[-0.005em]'}
          >
            {title}
          </h3>
          {description && (
            <div
              className={cn(
                'text-[13px] leading-[1.45] text-muted-foreground/80',
                variant === 'card' && '[overflow-wrap:anywhere]',
              )}
            >
              {description}
            </div>
          )}
        </div>
        <div
          role="group"
          aria-labelledby={id}
          className={cn(
            'flex min-w-0 max-w-full flex-wrap items-center gap-2 [&_input]:max-w-full [&_button]:max-w-full',
            variant === 'row' ? '@2xl:justify-end' : 'mt-auto justify-start',
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

export function SettingsRowsSkeleton({ label, rows = 2 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="divide-y divide-border/50">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} aria-hidden="true" className="flex min-h-14 items-center gap-6 px-4 py-3">
          <div className="min-w-0 flex-1 space-y-2">
            <div className="h-3.5 w-full max-w-52 animate-pulse rounded bg-muted motion-reduce:animate-none" />
            <div className="h-2.5 w-full max-w-72 animate-pulse rounded bg-muted/70 motion-reduce:animate-none" />
          </div>
          <div className="h-7 w-20 shrink-0 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
        </div>
      ))}
    </div>
  );
}
