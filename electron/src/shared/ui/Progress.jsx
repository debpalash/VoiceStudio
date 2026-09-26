import React from 'react';
import { Progress as ShadcnProgress } from '@/components/ui/progress.tsx';
import { cn } from '@/lib/utils';

const SIZES = { xs: 'h-[2px]', sm: 'h-[4px]', md: 'h-[6px]' };

const TONES = {
  brand: 'bg-[linear-gradient(90deg,var(--color-brand),var(--color-accent))]',
  success: 'bg-[linear-gradient(90deg,var(--color-success),var(--color-accent))]',
  warn: 'bg-[linear-gradient(90deg,var(--color-accent),var(--color-warn))]',
  danger: 'bg-[linear-gradient(90deg,var(--color-danger),var(--color-warn))]',
};

/**
 * Progress — determinate or indeterminate progress bar. Thin wrapper over the
 * shadcn/ui Progress (src/components/ui/progress.tsx, itself on
 * @radix-ui/react-progress) that preserves the legacy prop API and layers on the
 * per-tone gradient fill, sizes, shimmer overlay, and indeterminate mode. The
 * `ui-progress` / `ui-progress__fill` / `has-shimmer` / `is-indeterminate` class
 * hooks drive the shimmer + indeterminate keyframes in residual.css.
 *
 * @param value       0–100 when determinate. Omit for indeterminate.
 * @param tone        'brand' (default) | 'success' | 'warn' | 'danger'
 * @param size        'xs' | 'sm' | 'md'
 * @param shimmer     add moving highlight overlay (default true when determinate)
 */
export default function Progress({
  value,
  tone = 'brand',
  size = 'sm',
  shimmer,
  className = '',
  ...rest
}) {
  const isInvalid = value != null && (!Number.isFinite(value) || Number.isNaN(value));
  const safeValue = isInvalid ? null : value;
  const indeterminate = safeValue == null;
  const showShimmer = shimmer ?? !indeterminate;
  const clamped = indeterminate ? null : Math.max(0, Math.min(100, safeValue));

  return (
    <ShadcnProgress
      value={clamped}
      indeterminate={indeterminate}
      // `ui-progress` + `is-indeterminate` are retained class hooks for the
      // shimmer / indeterminate CSS rules in residual.css (keyframes + ::after).
      className={cn(
        'ui-progress rounded-sm',
        SIZES[size] ?? SIZES.sm,
        indeterminate && 'is-indeterminate',
        className,
      )}
      indicatorClassName={cn(
        'ui-progress__fill transition-[width] duration-[var(--dur-slow)] ease-[var(--ease-out)]',
        TONES[tone] ?? TONES.brand,
        showShimmer && 'has-shimmer',
      )}
      {...rest}
    />
  );
}
