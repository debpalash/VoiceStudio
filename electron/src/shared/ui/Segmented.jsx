import React from 'react';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group.tsx';
import { cn } from '@/lib/utils';

const ROOT =
  'ui-seg inline-flex gap-[2px] bg-bg-elev-2 p-[3px] rounded-[var(--radius-pill)] border border-transparent shrink';

const SIZE_MAP = { xs: 'segXs', sm: 'segSm' };

/**
 * Segmented — compact segmented control for small option sets. Thin wrapper over
 * the shadcn/ui ToggleGroup (src/components/ui/toggle-group.tsx, on
 * @radix-ui/react-toggle-group) using the `seg` variant. Preserves the legacy
 * prop API; keyboard nav + aria-pressed come from Radix. The option look is
 * styled with palette token utilities (brand-pink active fill) so it recolors
 * per theme.
 *
 * @param items    array of { value, label, title? }
 * @param value    currently selected `value`
 * @param onChange (value) => void
 * @param size     'xs' | 'sm'
 */
export default function Segmented({
  items = [],
  value,
  onChange,
  size = 'sm',
  className = '',
  ...rest
}) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(val) => {
        // Radix fires '' when you re-click the active item; ignore that
        if (val) onChange?.(val);
      }}
      variant="seg"
      size={SIZE_MAP[size] ?? SIZE_MAP.sm}
      className={cn(ROOT, className)}
      {...rest}
    >
      {items.map((item) => (
        <ToggleGroupItem key={item.value} value={item.value} title={item.title || undefined}>
          {item.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
