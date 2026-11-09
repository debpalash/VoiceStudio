import type { ComponentType, ReactNode } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * One collapsible group in the Stories / Audiobook setup pane. Every optional
 * group (cast, production overrides, cover, loudness, pronunciation) uses this
 * so they read as one tidy list: the same icon + label row as the always-open
 * fields above, a quiet summary on the right, and a chevron.
 *
 * A native <details> keeps the content mounted while closed (settings tests
 * and smokes read it) and gives keyboard toggling for free.
 */
export function SettingsSection({
  title,
  icon: Icon,
  meta,
  open,
  onToggle,
  className,
  children,
}: {
  title: ReactNode;
  icon?: ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' }>;
  meta?: ReactNode;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <details
      data-slot="settings-section"
      className={cn('group/section rounded-xl border border-border/60 bg-muted/20', className)}
      open={open}
      onToggle={(event) => onToggle?.(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-3 py-2.5 text-xs font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        {Icon && <Icon className="size-4 shrink-0" aria-hidden="true" />}
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {meta !== undefined && meta !== null && meta !== '' && (
          <span className="max-w-[45%] truncate font-normal tabular-nums text-muted-foreground/80">
            {meta}
          </span>
        )}
        <ChevronRightIcon
          className="size-3.5 shrink-0 transition-transform group-open/section:rotate-90 rtl:rotate-180 rtl:group-open/section:rotate-90"
          aria-hidden="true"
        />
      </summary>
      <div className="space-y-3 px-3 pt-1 pb-3">{children}</div>
    </details>
  );
}
