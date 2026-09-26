import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

/**
 * shadcn/ui Button — new-york style, themed through the VoiceStudio token bridge
 * (see index.css). `bg-primary`/`text-primary-foreground`/`bg-destructive`/
 * `border-input`/`ring-ring` resolve to the VoiceStudio palette, so this renders
 * in brand pink + amber accent + the dark chrome bg, and recolors with every
 * [data-theme].
 *
 * The CVA carries TWO families of variants:
 *   • the stock shadcn set (default / secondary / outline / ghost / link /
 *     destructive + sizes default / sm / lg / icon) — used by the foundation
 *     proof spec; and
 *   • the VoiceStudio set (primary / subtle / softGhost / danger / chip[+Active] /
 *     preset[+Active] / iconBtn[+Active] + sizes omniSm / omniMd / chip /
 *     preset / iconSm / iconMd) — these back the live `src/ui/Button.jsx`
 *     wrapper, which maps the legacy prop API onto them. They are expressed with
 *     palette token utilities (bg-primary, border-border, text-success, …) so
 *     every variant stays on-palette and recolors per theme.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 aria-invalid:border-destructive",
  {
    variants: {
      variant: {
        // ── stock shadcn ──
        default: 'bg-primary text-primary-foreground shadow-xs hover:bg-primary/90',
        destructive:
          'bg-destructive text-destructive-foreground shadow-xs hover:bg-destructive/90 focus-visible:ring-destructive/20',
        outline:
          'border border-transparent bg-background shadow-xs hover:bg-accent hover:text-accent-foreground',
        secondary: 'bg-secondary text-secondary-foreground shadow-xs hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline',

        // ── VoiceStudio (back the legacy src/ui/Button.jsx) ──
        // NB: app ships Tailwind WITHOUT Preflight, so a bare <button> keeps its
        // native UA border — every variant must set an explicit border (even a
        // transparent one) or the default chrome leaks through.
        primary:
          'border border-transparent bg-primary text-primary-foreground font-semibold shadow-xs hover:bg-primary/90 active:scale-[0.98]',
        subtle:
          'border border-transparent bg-transparent text-muted-foreground hover:bg-[var(--chrome-hover-bg)] hover:text-foreground hover:border-transparent',
        softGhost:
          'border border-transparent bg-transparent text-muted-foreground hover:bg-[var(--chrome-hover-bg)] hover:text-foreground',
        danger:
          'text-destructive bg-destructive/10 border border-transparent hover:bg-destructive/20 hover:border-transparent',
        chip: 'border border-transparent bg-transparent text-muted-foreground hover:bg-[var(--chrome-hover-bg)] hover:text-foreground hover:border-transparent',
        chipActive: 'text-success bg-success/10 border border-transparent',
        preset:
          'justify-start text-left border border-transparent bg-transparent text-muted-foreground hover:bg-[var(--chrome-hover-bg)] hover:text-foreground hover:border-transparent',
        presetActive:
          'justify-start text-left text-primary bg-primary/[0.12] border border-transparent',
        iconBtn:
          'border border-transparent bg-transparent text-muted-foreground hover:bg-[var(--chrome-hover-bg)] hover:text-foreground hover:border-transparent',
        iconBtnActive: 'text-primary bg-primary/[0.12] border border-transparent',
      },
      size: {
        // ── stock shadcn ──
        default: 'h-9 px-4 py-2 has-[>svg]:px-3',
        sm: 'h-8 rounded-md gap-1.5 px-3 has-[>svg]:px-2.5',
        lg: 'h-10 rounded-md px-6 has-[>svg]:px-4',
        icon: 'size-9',

        // ── VoiceStudio ──
        omniSm: 'px-2.5 py-[3px] text-xs',
        omniMd: 'px-3 py-1.5 text-sm',
        chip: 'px-2 py-0.5 text-xs',
        preset: 'px-2 py-[3px] text-xs',
        iconSm: 'size-5 p-0',
        iconMd: 'size-[22px] p-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : 'button';

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
