/**
 * One line item in a destructive Storage panel — used by both "Reset & remove"
 * and "Remove all data", so the two read as one system rather than two lists
 * that happen to sit next to each other.
 *
 * The design problem it solves: a flat list of sizes is unreadable. A 7.5 GB
 * model cache and a 391 B config file rendered at the same visual weight, so the
 * one number that actually matters — *where the space went* — was the one thing
 * you couldn't see. Each row now carries a **proportional bar**: its share of the
 * total being removed. The big one looks big.
 *
 * Rows are either informational (uninstall lists what it will delete) or
 * selectable (reset lets you pick). Pass `onToggle` to get a checkbox; leave it
 * off for a plain row.
 */
import { useTranslation } from 'react-i18next';
import { fmtBytes } from './bytes';

/**
 * @param {object}   props
 * @param {Function} props.icon      lucide icon component
 * @param {string}   props.label     what this is, in the user's words
 * @param {string}   [props.hint]    a second line — a caveat, not a repeat of the label
 * @param {string}   [props.path]    the folder on disk (dimmed; truncates, full text on hover)
 * @param {number}   [props.size]    bytes; omit for scopes that own no files
 * @param {number}   [props.share]   0–1, this row's fraction of the total. Drives the bar.
 * @param {boolean}  [props.checked]
 * @param {Function} [props.onToggle] present → the row is selectable
 * @param {boolean}  [props.disabled] nothing here to remove
 * @param {boolean}  [props.warn]     tint the bar as a caution (the shared model cache)
 */
export default function StorageTargetRow({
  icon: Icon,
  label,
  hint,
  path,
  size,
  share = 0,
  checked = false,
  onToggle,
  disabled = false,
  warn = false,
  testId,
}) {
  const { t } = useTranslation();
  const selectable = typeof onToggle === 'function';
  // An unticked row still shows its size, but claims none of the bar: the bars
  // must add up to what the button says it will free, or they are lying.
  const filled = selectable && !checked ? 0 : Math.max(0, Math.min(1, share));
  const Wrapper = selectable ? 'label' : 'div';

  return (
    <Wrapper
      // A selectable row's handle is its checkbox; an informational row has none,
      // so the id lands on the row itself. Either way `testId` addresses the thing
      // a test would actually interact with.
      data-testid={selectable ? undefined : testId}
      className={`flex items-start gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] border p-[var(--space-4)] transition-colors ${
        selectable && !disabled ? 'cursor-pointer' : ''
      } ${
        checked
          ? 'border-[color-mix(in_srgb,var(--chrome-accent)_32%,transparent)] bg-[var(--chrome-accent-bg)] shadow-[inset_2px_0_0_var(--chrome-accent)]'
          : 'border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] bg-[var(--chrome-bg)] hover:bg-[var(--chrome-hover-bg)]'
      } ${disabled ? 'opacity-50' : ''}`}
    >
      {selectable && (
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
          data-testid={testId}
          className="mt-[3px]"
        />
      )}

      <span
        className={`mt-[1px] flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--chrome-accent)_9%,transparent)] ${
          checked ? 'text-[var(--chrome-accent)]' : 'text-[var(--chrome-fg-muted)]'
        }`}
        aria-hidden="true"
      >
        {Icon && <Icon size={15} />}
      </span>

      <span className="flex min-w-0 flex-1 flex-col gap-[var(--space-1)]">
        <span className="flex items-baseline justify-between gap-[var(--space-3)]">
          <span className="[font-family:var(--font-sans)] text-[length:var(--text-md)] text-[var(--chrome-fg)]">
            {label}
          </span>
          <span className="shrink-0 [font-family:var(--font-mono)] text-[length:var(--text-sm)] tabular-nums text-[var(--chrome-fg-muted)]">
            {Number.isFinite(size) ? fmtBytes(size) : '—'}
          </span>
        </span>

        {/* Share of the total. Hidden when there is nothing to show, so rows that
            own no files (UI preferences) don't render an eternally empty track. */}
        {Number.isFinite(size) && size > 0 && (
          <span
            className="block h-[3px] w-full overflow-hidden rounded-[var(--radius-pill)] bg-[var(--chrome-hover-bg)]"
            role="presentation"
            data-testid={testId ? `${testId}-bar` : undefined}
          >
            <span
              className={`block h-full rounded-[var(--radius-pill)] ${
                warn ? 'bg-[var(--chrome-severity-warn)]' : 'bg-[var(--chrome-accent)]'
              }`}
              style={{ width: `${Math.round(filled * 100)}%` }}
            />
          </span>
        )}

        {hint && (
          <span className="[font-family:var(--font-sans)] text-[length:var(--text-xs)] leading-[1.5] text-[var(--chrome-fg-muted)]">
            {hint}
          </span>
        )}

        {path && (
          <span
            title={path}
            className="block truncate [font-family:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]"
          >
            {path}
          </span>
        )}

        {disabled && (
          <span className="[font-family:var(--font-sans)] text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]">
            {t('settings.storage_target_empty', { defaultValue: 'Nothing to remove' })}
          </span>
        )}
      </span>
    </Wrapper>
  );
}
