import * as React from 'react';
import * as ProgressPrimitive from '@radix-ui/react-progress';

import { cn } from '@/lib/utils';

/**
 * shadcn/ui Progress — new-york style, built on @radix-ui/react-progress for
 * correct ARIA value attributes, themed through the VoiceStudio token bridge
 * (`bg-secondary` track resolves to the dark elevated surface).
 *
 * Adapted from the stock shadcn source so it can back the legacy
 * `src/ui/Progress.jsx` wrapper, which adds per-tone gradient fills, sizes, a
 * shimmer overlay, and an indeterminate mode:
 *   • `indicatorClassName` styles the fill (the wrapper passes the tone gradient
 *     + shimmer hook classes there);
 *   • `indeterminate` omits the inline width so the unlayered
 *     `.ui-progress.is-indeterminate` rule (residual.css) can drive the sliding
 *     animation;
 *   • determinate fill uses a width style (not the stock translateX) so the
 *     shimmer overlay covers only the filled portion, matching the prior look.
 */
function Progress({
  className,
  indicatorClassName,
  value,
  indeterminate = false,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root> & {
  indicatorClassName?: string;
  indeterminate?: boolean;
}) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={indeterminate ? null : value}
      max={100}
      className={cn('relative w-full overflow-hidden rounded-full bg-secondary', className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className={cn('relative h-full transition-all', indicatorClassName)}
        style={indeterminate ? undefined : { width: `${value ?? 0}%` }}
      />
    </ProgressPrimitive.Root>
  );
}

export { Progress };
