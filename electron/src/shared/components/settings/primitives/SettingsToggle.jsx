import React from 'react';
import { cn } from '@/lib/utils';

/**
 * SettingsToggle — a token-styled accessible switch.
 *
 * FAST-mode shadcn migration: the track + knob are Tailwind utilities on the
 * VoiceStudio `--chrome-*` token bridge (palette preserved exactly); the old
 * `.st-toggle*` rules in primitives.css are gone. Still renders a real
 * `<input type="checkbox" role="switch">` (visually hidden) so label/role-based
 * queries and keyboard focus work; the visible track + knob is a sibling. The
 * on-state recolors via the `is-on` class on the label (arbitrary `[.is-on_&]`
 * variants on the children); focus uses the `peer` on the input.
 *
 * @param {boolean}   checked     on/off state
 * @param {function}  onChange    called with the next boolean value
 * @param {boolean=}  disabled    disable interaction
 * @param {string=}   id          id forwarded to the input (for an external <label htmlFor>)
 * @param {string=}   aria-label  accessible label when there's no visible <label>
 */
export default function SettingsToggle({
  checked,
  onChange,
  disabled = false,
  id,
  'aria-label': ariaLabel,
  ...rest
}) {
  return (
    <label
      className={cn(
        'relative inline-flex shrink-0 w-[42px] h-[24px] cursor-pointer',
        checked && 'is-on',
        disabled && 'is-disabled cursor-not-allowed opacity-50',
      )}
    >
      <input
        type="checkbox"
        role="switch"
        id={id}
        className="peer absolute inset-0 w-full h-full m-0 opacity-0 cursor-[inherit]"
        checked={!!checked}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(e) => onChange?.(e.target.checked)}
        {...rest}
      />
      <span
        className="absolute inset-0 rounded-[var(--radius-pill)] bg-[color-mix(in_srgb,var(--chrome-fg)_14%,var(--chrome-bg))] shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--chrome-fg)_8%,transparent)] transition-[background] duration-[160ms] ease-in-out [.is-on_&]:bg-[var(--color-brand)] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-[var(--color-ring)] peer-focus-visible:outline-offset-2"
        aria-hidden="true"
      >
        <span className="absolute top-[3px] left-[3px] w-[18px] h-[18px] rounded-full bg-[var(--chrome-fg-muted)] shadow-[var(--shadow-sm)] transition-[transform,background] duration-[160ms] ease-in-out [.is-on_&]:translate-x-[18px] [.is-on_&]:bg-[var(--chrome-bg)]" />
      </span>
    </label>
  );
}
