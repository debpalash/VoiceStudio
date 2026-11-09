import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * Gives a disabled control its reason. A disabled button ignores the pointer,
 * so the tooltip sits on a wrapper the pointer still reaches, and the same
 * text is the button's accessible description for keyboard and screen readers.
 *
 *   <DisabledReason reason={blocked ? t('…') : null}>
 *     {(describedBy) => <Button disabled={Boolean(blocked)} aria-describedby={describedBy} />}
 *   </DisabledReason>
 */
export function DisabledReason({
  reason,
  className,
  children,
}: {
  /** Why the control is unavailable; nothing when it is available. */
  reason?: string | null;
  className?: string;
  children: (describedBy: string | undefined) => ReactNode;
}) {
  const id = useId();
  if (!reason) return <>{children(undefined)}</>;
  return (
    <span data-slot="disabled-reason" title={reason} className={cn('inline-flex', className)}>
      {children(id)}
      <span id={id} className="sr-only">
        {reason}
      </span>
    </span>
  );
}

/** The first reason that applies, in priority order; null when every check passes. */
export function firstReason(checks: readonly (readonly [boolean, string])[]): string | null {
  return checks.find(([blocked]) => blocked)?.[1] ?? null;
}
