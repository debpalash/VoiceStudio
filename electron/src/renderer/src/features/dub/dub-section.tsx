import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** The Dubbing panel's card: same radius, padding and quiet border as the Design panel. */
export const DUB_CARD = 'rounded-xl border border-border/40 bg-muted/15 p-3';

/** A numbered step heading with an optional action at its end. */
export function DubStepHeading({
  step,
  children,
  end,
}: {
  step: number;
  children: ReactNode;
  end?: ReactNode;
}) {
  return (
    <div className="flex min-h-7 items-center gap-2">
      <h2 className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium text-foreground/95">
        <span
          aria-hidden="true"
          className="grid size-5 shrink-0 place-items-center rounded-full bg-primary/12 text-[11px] font-semibold text-primary"
        >
          {step}
        </span>
        <span className="truncate">{children}</span>
      </h2>
      {end}
    </div>
  );
}

/** A compact label-and-control row; the control keeps its natural width. */
export function DubFieldRow({
  label,
  htmlFor,
  hint,
  children,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  /** Full help text, shown as the label's tooltip. */
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  const Tag = htmlFor ? 'label' : 'span';
  return (
    <div className={cn('flex min-h-9 min-w-0 items-center gap-2', className)}>
      <Tag htmlFor={htmlFor} title={hint} className="flex min-w-0 flex-1">
        {/* The inner span carries the colour: global label styles outrank utilities. */}
        <span className="truncate text-xs font-medium text-muted-foreground">{label}</span>
      </Tag>
      {children}
    </div>
  );
}

/** One line of secondary help; the full text stays readable on hover and to screen readers. */
export function DubHint({ children, id }: { children: string; id?: string }) {
  return (
    <p id={id} title={children} className="line-clamp-1 text-xs text-muted-foreground">
      {children}
    </p>
  );
}
