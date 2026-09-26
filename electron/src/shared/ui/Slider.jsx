import React, { forwardRef, useId } from 'react';
import { cn } from '@/lib/utils';
import { Slider as ShadcnSlider } from '@/components/ui/slider.tsx';

/**
 * Slider — styled horizontal range input.
 * Backed by the shadcn <Slider> (which wraps @radix-ui/react-slider for full
 * keyboard accessibility — arrow keys, Home/End — and ARIA value announcements),
 * with the VoiceStudio label + value-bubble chrome around it. Prop API unchanged.
 *
 * @param value       controlled number
 * @param onChange    receives the new number (not the event)
 * @param min, max, step standard range props
 * @param format      optional (v) => string for the value bubble
 * @param showValue   show the trailing value bubble (default true)
 * @param label       optional small label above the track
 * @param size        'sm' | 'md'
 */
const Slider = forwardRef(function Slider(
  {
    value,
    onChange,
    min = 0,
    max = 100,
    step = 1,
    format = (v) => v,
    showValue = true,
    label = null,
    size = 'md',
    className = '',
    ...rest
  },
  ref,
) {
  const id = useId();
  const isSm = size === 'sm';

  return (
    <div className={`flex w-full flex-col gap-[var(--space-1)] ${className}`}>
      {label && (
        <label
          htmlFor={id}
          className="text-[length:var(--text-xs)] font-semibold tracking-[0.02em] text-fg-muted"
        >
          {label}
        </label>
      )}
      <div className="flex items-center gap-[var(--space-3)]">
        <ShadcnSlider
          ref={ref}
          id={id}
          // Tighten the shadcn track to the VoiceStudio scale via the data-slot
          // selectors the component exposes; keeps the thin-track look.
          className={cn(
            'flex-1 cursor-pointer',
            isSm
              ? '[&_[data-slot=slider-track]]:h-[2px] [&_[data-slot=slider-thumb]]:size-2.5'
              : '[&_[data-slot=slider-track]]:h-[3px] [&_[data-slot=slider-thumb]]:size-3',
          )}
          value={[Number(value)]}
          onValueChange={([v]) => onChange?.(v)}
          min={min}
          max={max}
          step={step}
          {...rest}
        />
        {showValue && (
          <span
            className={`min-w-[2em] shrink-0 rounded-sm border border-border bg-bg-elev-2 text-center font-mono text-fg tabular-nums ${
              isSm
                ? 'px-1 py-0 text-[length:var(--text-2xs)]'
                : 'px-[5px] py-px text-[length:var(--text-xs)]'
            }`}
            aria-live="polite"
          >
            {format(value)}
          </span>
        )}
      </div>
    </div>
  );
});

export default Slider;
