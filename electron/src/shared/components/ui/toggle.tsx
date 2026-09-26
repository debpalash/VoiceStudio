import * as React from 'react';
import * as TogglePrimitive from '@radix-ui/react-toggle';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

/**
 * shadcn/ui Toggle — new-york style, themed through the VoiceStudio token bridge.
 *
 * `toggleVariants` is shared with ToggleGroup. Beyond the stock variants
 * (default / outline + sizes default / sm / lg) it carries the VoiceStudio
 * segmented-control option (`variant="seg"` + sizes segXs / segSm) that backs
 * the legacy `src/ui/Segmented.jsx` wrapper: a borderless pill whose on/off look
 * keys off Radix's `data-state`, styled with palette token utilities so it
 * recolors per theme (brand-pink fill when active).
 */
const toggleVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium hover:bg-muted hover:text-muted-foreground disabled:pointer-events-none disabled:opacity-50 data-[state=on]:bg-accent data-[state=on]:text-accent-foreground [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 [&_svg]:shrink-0 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] outline-none transition-[color,box-shadow] aria-invalid:ring-destructive/20 aria-invalid:border-destructive whitespace-nowrap",
  {
    variants: {
      variant: {
        default: 'bg-transparent',
        outline:
          'border border-input bg-transparent shadow-xs hover:bg-accent hover:text-accent-foreground',
        // ── VoiceStudio segmented option ──
        seg: 'font-extrabold border-0 cursor-pointer rounded-[var(--radius-pill)] bg-transparent text-fg-subtle transition-[background,color] duration-[var(--dur-fast)] ease-[var(--ease-out)] data-[state=off]:hover:text-fg data-[state=off]:hover:bg-[var(--chrome-hover-bg)] data-[state=on]:bg-primary/25 data-[state=on]:text-fg',
      },
      size: {
        default: 'h-9 px-2 min-w-9',
        sm: 'h-8 px-1.5 min-w-8',
        lg: 'h-10 px-2.5 min-w-10',
        // ── VoiceStudio segmented sizes ──
        segXs: 'px-[9px] py-[2px] text-[0.58rem]',
        segSm: 'px-[10px] py-[3px] text-[0.62rem]',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

function Toggle({
  className,
  variant,
  size,
  ...props
}: React.ComponentProps<typeof TogglePrimitive.Root> & VariantProps<typeof toggleVariants>) {
  return (
    <TogglePrimitive.Root
      data-slot="toggle"
      className={cn(toggleVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Toggle, toggleVariants };
