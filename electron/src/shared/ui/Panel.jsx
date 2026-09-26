import React, { forwardRef } from 'react';
import { Card } from '@/components/ui/card';
// The root surface is shadcn/ui Card (src/components/ui/card.tsx). Card runs the
// class merge through cn()/twMerge, so the resets below (block / gap-0 / p-0 /
// rounded-lg + the border/bg variant utilities) cleanly override Card's
// opinionated defaults (flex / gap-6 / py-6 / rounded-xl / bg-card / shadow-sm),
// and the result is forwarded to the requested `as` tag via Card's `asChild`.
//
// residual.css still holds ONLY the glass variant's backdrop-filter + gradient
// surface + ::before highlight (effects Tailwind utilities can't express); being
// unlayered, `.ui-panel--glass` wins over Card's layered `bg-card`. The
// `.ui-panel*` class names are kept so that rule and external contextual
// overrides (`.glossary-panel .ui-panel__body`, `.voice-profile__hero
// .ui-panel__body`) still match.

const VARIANT = {
  // glass: surface (gradients + backdrop-filter) + ::before stay in residual.css.
  glass: 'border border-transparent',
  solid:
    'border border-transparent ' +
    '[background-image:linear-gradient(160deg,#2a2624_0%,#201c1b_100%)] [box-shadow:var(--shadow-md)]',
  flat: 'border border-transparent [background-color:rgba(0,0,0,0.08)]',
};

const PAD = {
  none: 'p-0',
  sm: 'p-[var(--space-4)]',
  md: 'p-[var(--space-5)]',
  lg: 'p-[var(--space-6)]',
};

/**
 * Panel — a content surface, backed by shadcn/ui Card. Replaces the ad-hoc card
 * divs + `.glass-panel`.
 *
 * @param variant 'glass' | 'solid' | 'flat'
 * @param padding 'none' | 'sm' | 'md' | 'lg'
 * @param title   optional string or node rendered in the panel header
 * @param actions optional node rendered on the right of the header
 * @param as      element tag ('div' | 'section' | 'article' …)
 */
const Panel = forwardRef(function Panel(
  {
    variant = 'glass',
    padding = 'md',
    title = null,
    actions = null,
    as: Tag = 'section',
    className = '',
    children,
    ...rest
  },
  ref,
) {
  const classes = [
    'ui-panel',
    `ui-panel--${variant}`,
    // resets for Card's opinionated root defaults + Panel's own surface.
    'block gap-0 p-0 relative overflow-hidden rounded-lg text-fg',
    VARIANT[variant] || VARIANT.glass,
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const hasHeader = title != null || actions != null;
  const bodyClasses = ['ui-panel__body', PAD[padding] || PAD.md, hasHeader && 'pt-[var(--space-4)]']
    .filter(Boolean)
    .join(' ');

  return (
    <Card asChild className={classes}>
      <Tag ref={ref} {...rest}>
        {hasHeader && (
          <header className="ui-panel__header flex items-center justify-between py-[var(--space-4)] px-[var(--space-5)] gap-[var(--space-4)] [border-bottom:1px_solid_transparent]">
            {title != null && (
              <div className="ui-panel__title flex items-center gap-[var(--space-3)] min-w-0 [font-size:var(--text-md)] font-bold text-fg tracking-[-0.01em]">
                {title}
              </div>
            )}
            {actions != null && (
              <div className="ui-panel__actions flex items-center gap-[var(--space-3)] shrink-0">
                {actions}
              </div>
            )}
          </header>
        )}
        <div className={bodyClasses}>{children}</div>
      </Tag>
    </Card>
  );
});

export default Panel;
