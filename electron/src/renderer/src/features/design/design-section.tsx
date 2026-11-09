import type { ReactNode, Ref } from 'react';
import { ChevronDownIcon, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/** The Design panel's one card style: same radius, padding and quiet border everywhere. */
export const DESIGN_CARD = 'rounded-xl border border-border/40 bg-muted/15 p-3';

/** A section heading row: icon, title, then whatever sits at its end. */
export function DesignHeading({
  icon: Icon,
  children,
  htmlFor,
}: {
  icon: LucideIcon;
  children: ReactNode;
  htmlFor?: string;
}) {
  const Tag = htmlFor ? 'label' : 'span';
  return (
    <Tag
      htmlFor={htmlFor}
      className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium text-foreground/95"
    >
      <Icon className="size-4 shrink-0 text-primary/80" aria-hidden="true" />
      <span className="truncate">{children}</span>
    </Tag>
  );
}

/** Every collapsible on the page looks and behaves the same. */
export function DesignDisclosure({
  icon,
  title,
  meta,
  open,
  onToggle,
  detailsRef,
  children,
  className,
}: {
  icon: LucideIcon;
  title: ReactNode;
  /** A one-line summary shown while collapsed or open, truncated with a tooltip. */
  meta?: string;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  detailsRef?: Ref<HTMLDetailsElement>;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details
      ref={detailsRef}
      open={open}
      onToggle={onToggle ? (event) => onToggle(event.currentTarget.open) : undefined}
      className={cn('group', DESIGN_CARD, 'py-1.5', className)}
    >
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
        <DesignHeading icon={icon}>{title}</DesignHeading>
        {meta ? (
          <span
            className="min-w-0 max-w-[55%] truncate text-xs font-normal text-muted-foreground"
            title={meta}
          >
            {meta}
          </span>
        ) : null}
        <ChevronDownIcon
          aria-hidden="true"
          className="size-4 shrink-0 text-muted-foreground transition-transform duration-150 group-open:rotate-180 motion-reduce:transition-none"
        />
      </summary>
      <div className="pt-2 pb-1.5">{children}</div>
    </details>
  );
}
