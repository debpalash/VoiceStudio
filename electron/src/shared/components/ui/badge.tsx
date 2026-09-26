import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

/**
 * shadcn/ui Badge — new-york style, themed through the VoiceStudio token bridge.
 *
 * Beyond the stock shadcn variants (default / secondary / destructive /
 * outline) the CVA carries the VoiceStudio *tones* (neutral / brand / success /
 * warn / danger / info / violet) that back the legacy `src/ui/Badge.jsx`
 * wrapper. Tones render as chrome chips — a mono uppercase pill with a
 * tinted fill (borders removed app-wide) — using palette token utilities so
 * each tone recolors with every [data-theme].
 */
const badgeVariants = cva(
  'inline-flex items-center gap-[2px] rounded-[var(--chrome-radius-pill)] font-mono font-semibold tracking-[var(--chrome-label-track)] uppercase whitespace-nowrap select-none leading-[1.2] [&>svg]:size-3 [&>svg]:pointer-events-none',
  {
    variants: {
      variant: {
        // ── stock shadcn ──
        default: 'border border-transparent bg-primary text-primary-foreground',
        secondary: 'border border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border border-transparent bg-destructive text-destructive-foreground',
        outline: 'border border-transparent bg-secondary text-foreground',
        // ── VoiceStudio tones ──
        neutral: 'text-muted-foreground border border-transparent bg-transparent',
        brand: 'text-primary border border-transparent bg-primary/[0.12]',
        success: 'text-success border border-transparent bg-success/10',
        warn: 'text-accent border border-transparent bg-accent/10',
        danger: 'text-destructive border border-transparent bg-destructive/10',
        info: 'text-info border border-transparent bg-info/10',
        violet: 'text-muted-foreground border border-transparent bg-transparent',
      },
      size: {
        xs: 'px-1.5 py-0 text-[11px]',
        sm: 'px-[7px] py-px text-[11px]',
      },
    },
    defaultVariants: {
      variant: 'neutral',
      size: 'sm',
    },
  },
);

function Badge({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<'span'> &
  VariantProps<typeof badgeVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : 'span';

  return (
    <Comp
      data-slot="badge"
      className={cn(badgeVariants({ variant, size }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
