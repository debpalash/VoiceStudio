import React from 'react';
import { cn } from '@/lib/utils';
import InfoHint from './InfoHint';

/**
 * SettingRow — one labelled row in a SettingsSection.
 *
 * FAST-mode shadcn migration: the grid layout, label/subtitle typography and the
 * right-aligned control slot are now Tailwind utilities on the VoiceStudio
 * `--chrome-*` / `--space-*` token bridge (palette preserved exactly). The old
 * `.st-row*` rules in primitives.css are gone. Row-stacking on a narrow Settings
 * column is reproduced with the Tailwind v4 named-container variant
 * `@max-[600px]/settings:` (the `settings` container lives on Settings.jsx), with
 * the legacy `max-[560px]:` viewport query as the out-of-container fallback.
 *
 * Layout: [ icon? + title + one-line subtitle ] [ control ].
 *
 * @param {LucideIcon=} icon     optional leading icon — rendered inline (size 14, dim) inside the title
 * @param {ReactNode}   title    the row label (already translated)
 * @param {ReactNode=}  subtitle optional muted description line under the title (wraps cleanly)
 * @param {ReactNode=}  note     alias for `subtitle` — fallback when `subtitle` is absent.
 *                               A row renders AT MOST ONE muted description line.
 * @param {ReactNode=}  hint     optional long help prose / Learn-more link — rendered as an InfoHint
 * @param {ReactNode}   control  the right-aligned control or value
 * @param {boolean=}    mono     render the control monospace (read-only data value)
 * @param {boolean=}    stack    full-width stacked layout: label on top, control row below
 *                               (replaces the old `st-row--stack` className)
 * @param {'center'|'start'=} align vertical alignment of the control (default 'center')
 * @param {string=}     className extra class on the row
 */
export default function SettingRow({
  icon: Icon,
  title,
  subtitle,
  note,
  hint,
  control,
  mono = false,
  stack = false,
  align = 'center',
  className = '',
}) {
  return (
    <div
      data-slot="setting-row"
      data-mono={mono ? '' : undefined}
      className={cn(
        'grid min-h-0 gap-y-[1px] rounded-[var(--chrome-radius-pill)] border-b border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] px-[var(--space-3)] py-[var(--space-5)] [font-family:var(--font-sans)] last:border-b-0',
        align === 'start' ? 'items-start' : 'items-center',
        stack
          ? 'grid-cols-[1fr] gap-[var(--space-3)]'
          : 'grid-cols-[minmax(0,1fr)_auto] gap-x-[var(--space-5)] @min-[601px]/settings:has-[input:not([type=checkbox]):not([type=radio]):not([type=range])]:[grid-template-columns:minmax(0,1fr)_minmax(0,1.9fr)] @min-[601px]/settings:has-[select]:[grid-template-columns:minmax(0,1fr)_minmax(0,1.9fr)] @min-[601px]/settings:has-[textarea]:[grid-template-columns:minmax(0,1fr)_minmax(0,1.9fr)] @max-[600px]/settings:grid-cols-[1fr] @max-[600px]/settings:gap-[var(--space-2)] max-[560px]:grid-cols-[1fr] max-[560px]:gap-[var(--space-2)]',
        className,
      )}
    >
      <div className={cn('min-w-0 flex flex-col gap-[2px]', stack ? 'max-w-none' : 'max-w-[52ch]')}>
        <span className="inline-flex items-center gap-[var(--space-2)] text-[length:var(--text-md)] font-medium leading-[1.35] text-[color:var(--chrome-fg)]">
          {Icon && (
            <span
              data-slot="setting-row-icon"
              className="inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--chrome-accent)_9%,transparent)] text-[var(--chrome-accent)]"
              aria-hidden="true"
            >
              <Icon size={13} />
            </span>
          )}
          {title}
          {hint && <InfoHint>{hint}</InfoHint>}
        </span>
        {(subtitle || note) && (
          <span className="text-[color:var(--chrome-fg-dim)] text-[length:var(--text-xs)] leading-[1.45] whitespace-normal [text-wrap:pretty] [&_a]:text-[var(--chrome-accent)] [&_a]:no-underline [&_a:hover]:underline">
            {subtitle || note}
          </span>
        )}
      </div>
      {control != null && (
        <div
          data-slot="setting-row-control"
          className={cn(
            'inline-flex items-center gap-[var(--space-3)] min-w-0 box-border pr-[2px]',
            '[&_input:not([type=checkbox]):not([type=radio]):not([type=range])]:min-w-0 [&_input:not([type=checkbox]):not([type=radio]):not([type=range])]:max-w-full [&_select]:min-w-0 [&_select]:max-w-full [&_textarea]:min-w-0 [&_textarea]:max-w-full',
            stack
              ? 'justify-self-stretch max-w-full text-left flex-wrap whitespace-normal [&_[data-slot=settings-input]]:max-w-full [&_[data-slot=settings-input]]:flex-[1_1_220px]'
              : 'justify-self-end max-w-[85%] text-right whitespace-nowrap has-[input:not([type=checkbox]):not([type=radio]):not([type=range])]:w-full has-[select]:w-full has-[textarea]:w-full @max-[600px]/settings:justify-self-start @max-[600px]/settings:w-full @max-[600px]/settings:max-w-full @max-[600px]/settings:text-left @max-[600px]/settings:whitespace-normal max-[560px]:justify-self-start max-[560px]:w-full max-[560px]:max-w-full max-[560px]:text-left',
            mono &&
              '[font-family:var(--chrome-font-mono)] text-[length:var(--text-base)] text-[color:var(--chrome-fg-muted)] tabular-nums max-w-[42ch] whitespace-normal [word-break:normal] [overflow-wrap:break-word] leading-[1.45]',
          )}
        >
          {control}
        </div>
      )}
    </div>
  );
}
