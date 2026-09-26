import React from 'react';

/**
 * FooterBtn — the tinted-outline action button family used in the dub header /
 * footer action strip. Flat chrome surfaces: color = accent (text + border +
 * faint fill), not a gradient. Pure Tailwind utilities on the palette tokens —
 * no global .btn-primary / .dub-footer-btn CSS. forwardRef so <Menu> can wire
 * its triggerRef to the underlying button (the Export menu needs it to open).
 */
const BASE =
  'inline-flex items-center justify-center gap-[5px] flex-1 mt-0 ' +
  'font-[family-name:var(--font-sans)] tracking-[0.02em] normal-case ' +
  'bg-transparent border rounded-[var(--chrome-radius-pill)] shadow-none touch-manipulation ' +
  'transition-[transform,background,color,box-shadow] duration-150 motion-reduce:transition-none ' +
  'focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)] ' +
  'disabled:opacity-45 disabled:cursor-not-allowed';

const TONES = {
  idle: 'text-[var(--chrome-fg-muted)] border-transparent hover:bg-[var(--chrome-hover-bg)]',
  stopping: 'text-[var(--chrome-fg-muted)] border-transparent hover:bg-[var(--chrome-hover-bg)]',
  danger:
    'text-[var(--chrome-severity-err)] border-transparent bg-[color-mix(in_srgb,var(--chrome-severity-err)_10%,transparent)] hover:bg-[color-mix(in_srgb,var(--chrome-severity-err)_18%,transparent)]',
  green:
    'text-[var(--chrome-severity-ok)] border-transparent bg-[color-mix(in_srgb,var(--chrome-severity-ok)_10%,transparent)] hover:bg-[color-mix(in_srgb,var(--chrome-severity-ok)_18%,transparent)]',
  pink: 'text-[var(--chrome-accent)] border-[var(--chrome-accent-border)] bg-[var(--chrome-accent-bg)] hover:bg-[color-mix(in_srgb,var(--chrome-accent)_20%,transparent)]',
  blue: 'text-[var(--color-info)] border-transparent bg-[color-mix(in_srgb,var(--color-info)_10%,transparent)]',
  lime: 'text-[#b8bb26] border-transparent bg-[color-mix(in_srgb,#b8bb26_10%,transparent)]',
  amber:
    'text-[var(--chrome-severity-warn)] border-transparent bg-[color-mix(in_srgb,var(--chrome-severity-warn)_10%,transparent)]',
  orange:
    'text-[var(--color-warn)] border-transparent bg-[color-mix(in_srgb,var(--color-warn)_10%,transparent)]',
};

const FooterBtn = React.forwardRef(function FooterBtn(
  { tone = 'idle', sm = false, disabled, onClick, icon, label, className = '', ...rest },
  ref,
) {
  const size = sm ? 'px-[6px] py-[3px] text-[0.62rem]' : 'px-[8px] py-[5px] text-[0.72rem]';
  const cls = [BASE, size, TONES[tone] || TONES.idle, className].filter(Boolean).join(' ');
  return (
    <button type="button" ref={ref} className={cls} disabled={disabled} onClick={onClick} {...rest}>
      {icon} {label}
    </button>
  );
});

export default FooterBtn;
