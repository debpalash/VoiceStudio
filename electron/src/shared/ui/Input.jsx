import React, { forwardRef, useId } from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Input as ShadcnInput, inputBaseClass } from '@/components/ui/input.tsx';
import { Textarea as ShadcnTextarea } from '@/components/ui/textarea.tsx';
// The native <select> caret (appearance reset + data-URI arrow background + its
// hover border) lives in src/styles/residual.css under `.ui-select` — an SVG
// data-URI background that's impractical to express as a utility.

/* VoiceStudio form primitives, now BACKED BY shadcn/ui (src/components/ui/*) while
 * keeping the exact same exports + prop APIs the app already imports:
 *   import { Field, Input, Textarea, Select } from '../ui';
 *
 * Input / Textarea render the shadcn components (palette-coherent via the token
 * bridge in index.css — chrome border, brand focus ring, destructive invalid
 * state, all theme-tracking). Select stays a NATIVE <select> — many call sites
 * (DubSegmentTable, CompareModal, GeneralTab) depend on `onChange={(e) =>
 * …e.target.value}`, which Radix's value-only Select would break — but is given
 * the same shadcn shell (`inputBaseClass`) so all three look identical.
 *
 * `fieldSizeVariants` is the VoiceStudio size scale layered over the shadcn shell:
 * it swaps the shell's fixed `h-9` for padding-based sizing (the established
 * compact look) and restores the filled `bg-bg-elev-2` surface. Named utilities
 * only, so tailwind-merge resolves them cleanly over the shell defaults; the
 * `md:` font-size variants override the shell's responsive `md:text-sm`. */
const fieldSizeVariants = cva('h-auto bg-bg-elev-2', {
  variants: {
    size: {
      sm: 'px-1.5 py-0.5 text-xs md:text-xs',
      md: 'px-2 py-1 text-sm md:text-sm',
      lg: 'px-2.5 py-1.5 text-base md:text-base',
    },
  },
  defaultVariants: { size: 'md' },
});

/**
 * Field — optional wrapper for label + input + hint/error.
 *
 * @param label   string rendered above the control
 * @param hint    small muted helper text below
 * @param error   string error message (overrides hint, adds error state)
 * @param icon    optional leading icon node (for Input variant)
 */
export function Field({ label, hint, error, icon, children }) {
  const id = useId();
  const describedBy = error ? `${id}-err` : hint ? `${id}-hint` : undefined;

  const enriched = React.Children.map(children, (child) => {
    if (!React.isValidElement(child)) return child;
    const props = {
      id: child.props.id || id,
      'aria-invalid': error ? true : undefined,
      'aria-describedby': describedBy,
    };
    // Replaces the `:has(.ui-field__icon) .ui-input { padding-left }` selector:
    // when an icon is present, push the control's text past it.
    if (icon) props.className = cn(child.props.className, 'pl-[22px]');
    return React.cloneElement(child, props);
  });

  return (
    <div className="ui-field flex flex-col gap-[var(--space-1)] min-w-0">
      {label && (
        <label
          htmlFor={id}
          className="ui-field__label [font-size:var(--text-xs)] font-semibold text-fg-muted tracking-[0.02em]"
        >
          {label}
        </label>
      )}
      <div className="ui-field__control relative flex items-center">
        {icon && (
          <span
            className="ui-field__icon absolute left-[7px] inline-flex text-fg-subtle pointer-events-none"
            aria-hidden="true"
          >
            {icon}
          </span>
        )}
        {enriched}
      </div>
      {error && (
        <div
          id={`${id}-err`}
          className="ui-field__error [font-size:var(--text-2xs)] text-danger mt-[var(--space-1)] font-medium"
        >
          {error}
        </div>
      )}
      {!error && hint && (
        <div
          id={`${id}-hint`}
          className="ui-field__hint [font-size:var(--text-2xs)] text-fg-subtle mt-[var(--space-1)]"
        >
          {hint}
        </div>
      )}
    </div>
  );
}

/**
 * Input — text / number / email / url input.
 * Backed by the shadcn <Input> shell + the VoiceStudio size scale.
 */
export const Input = forwardRef(function Input({ size = 'md', className = '', ...rest }, ref) {
  return <ShadcnInput ref={ref} className={cn(fieldSizeVariants({ size }), className)} {...rest} />;
});

/**
 * Textarea — multi-line input with optional auto-sizing.
 * Backed by the shadcn <Textarea> shell; `field-sizing-fixed` keeps the classic
 * rows-driven sizing the call sites expect (shadcn defaults to content-sizing).
 */
export const Textarea = forwardRef(function Textarea(
  { size = 'md', rows = 3, className = '', ...rest },
  ref,
) {
  return (
    <ShadcnTextarea
      ref={ref}
      rows={rows}
      className={cn(
        fieldSizeVariants({ size }),
        'field-sizing-fixed min-h-[60px] resize-y leading-[1.5]',
        className,
      )}
      {...rest}
    />
  );
});

/**
 * Select — styled NATIVE select (keeps keyboard + accessibility + the
 * `onChange={(e) => …}` event shape every call site relies on). Wears the same
 * shadcn shell as Input via `inputBaseClass`; the `ui-select` class carries the
 * caret (data-URI arrow) from residual.css. `block` neutralises the shell's
 * `flex` so the native control renders normally.
 */
export const Select = forwardRef(function Select(
  { size = 'md', className = '', children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn(
        inputBaseClass,
        fieldSizeVariants({ size }),
        'ui-select block cursor-pointer',
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
});
