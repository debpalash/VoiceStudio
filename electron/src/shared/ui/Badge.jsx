import React from 'react';
import { Badge as ShadcnBadge } from '@/components/ui/badge.tsx';
import { cn } from '@/lib/utils';

// Legacy tone names that the shadcn Badge CVA understands as `variant` values.
const TONES = ['neutral', 'brand', 'success', 'warn', 'danger', 'info', 'violet'];

/**
 * Badge — small status pill. Thin wrapper over the shadcn/ui Badge
 * (src/components/ui/badge.tsx) that preserves the legacy prop API: the `tone`
 * maps to the shadcn `variant`, `size` passes straight through, and `dot`
 * renders the leading status dot. Each tone is styled with palette token
 * utilities in the CVA, so it recolors with every [data-theme].
 *
 * @param tone 'neutral' | 'brand' | 'success' | 'warn' | 'danger' | 'info' | 'violet'
 * @param size 'xs' | 'sm'
 */
export default function Badge({
  tone = 'neutral',
  size = 'sm',
  dot = false,
  className = '',
  children,
  ...rest
}) {
  const variant = TONES.includes(tone) ? tone : 'neutral';
  return (
    // `ui-badge` is retained so the externally-applied `.ui-badge--pulse`
    // modifier (Header status badge) can still animate the dot via CSS.
    <ShadcnBadge variant={variant} size={size} className={cn('ui-badge', className)} {...rest}>
      {/* `ui-badge__dot` retained so `.ui-badge--pulse .ui-badge__dot` keyframes
          (residual.css) can still drive the pulse animation. */}
      {dot && (
        <span
          className="ui-badge__dot h-[5px] w-[5px] rounded-full bg-current"
          aria-hidden="true"
        />
      )}
      {children}
    </ShadcnBadge>
  );
}
