import React from 'react';
import { Tooltip as ShadcnTooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * Tooltip — keyboard-accessible replacement for `title=`.
 * Backed by shadcn/ui Tooltip (which wraps @radix-ui/react-tooltip) for
 * collision-aware positioning + enter/exit animation.
 *
 * @param content    tooltip body (string or node)
 * @param placement  'top' | 'bottom' | 'left' | 'right'
 * @param delay      ms before showing (default 300)
 */
export default function Tooltip({ content, placement = 'top', delay = 300, children }) {
  if (!content) return children;

  // Map our placement names to Radix side names
  const sideMap = { top: 'top', bottom: 'bottom', left: 'left', right: 'right' };
  const side = sideMap[placement] || 'top';

  // shadcn's <Tooltip> supplies its own TooltipProvider; passing delayDuration
  // to the Root overrides that provider's default so the `delay` prop is honored.
  return (
    <ShadcnTooltip delayDuration={delay}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} sideOffset={5} showArrow={false} className="ui-tooltip">
        {content}
      </TooltipContent>
    </ShadcnTooltip>
  );
}
