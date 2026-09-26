import React, { useId } from 'react';

/**
 * SettingsSection — the standard header + body wrapper for every Settings card.
 *
 * FAST-mode shadcn migration: the surface, header, icon tile, titles and actions
 * are now Tailwind utilities layered on the VoiceStudio `--chrome-*` / `--space-*`
 * token bridge (palette preserved exactly — no hardcoded colors). The old
 * `.st-section*` rules in primitives.css are gone.
 *
 * Renders an icon-tile + title header, an optional wrapping description,
 * optional right-aligned actions, then the children body.
 *
 * @param {LucideIcon} icon        lucide icon component (rendered at size 15)
 * @param {string}     title       section title (already translated)
 * @param {string=}    description optional wrapping subtitle (muted/dim)
 * @param {string=}    accent      optional CSS color for the icon tile (defaults to the page accent)
 * @param {ReactNode=} actions     optional right-aligned header actions (buttons, badges…)
 * @param {ReactNode}  children    section body
 * @param {string=}    className   extra class on the root <section>
 * @param {string=}    contentClassName extra class on the body wrapper
 * @param {boolean=}   compact     reduce card/header padding for dense utility panels
 */

// Shared so the raw card surfaces in EnginesTab / ModelStoreTab (which carry a
// custom toolbar + table instead of the icon/title header) stay byte-identical
// to the primitive without re-deriving the token string. `data-slot` is the
// stable hook Settings.css / panel CSS reach into.
export const SETTINGS_SECTION_SURFACE =
  'bg-[color-mix(in_srgb,var(--chrome-fg)_3%,var(--chrome-bg))] border border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] rounded-[calc(var(--chrome-radius-pill)*1.4)] shadow-[0_1px_0_color-mix(in_srgb,var(--chrome-fg)_3%,transparent)] px-[var(--space-6)] py-[var(--space-6)] mb-[var(--space-5)] last:mb-0';

export default function SettingsSection({
  icon: Icon,
  title,
  description,
  accent,
  actions,
  children,
  className = '',
  contentClassName = '',
  compact = false,
}) {
  const headingId = useId();

  return (
    <section
      data-slot="settings-section"
      aria-labelledby={headingId}
      className={`${SETTINGS_SECTION_SURFACE} ${
        compact ? '!mb-[var(--space-3)] !px-[var(--space-4)] !py-[var(--space-4)]' : ''
      } ${className}`.trim()}
    >
      <header
        className={`${
          compact
            ? 'mb-[var(--space-3)] pb-[var(--space-3)]'
            : 'mb-[var(--space-5)] pb-[var(--space-4)]'
        } flex items-center gap-[var(--space-4)] border-b border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] @max-[560px]/settings:flex-wrap`}
      >
        {Icon && (
          <span
            data-slot="settings-section-icon"
            className="inline-flex h-[28px] w-[28px] shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] border border-transparent bg-[color-mix(in_srgb,currentColor_12%,var(--chrome-bg))] text-[color:var(--chrome-accent)]"
            style={accent ? { color: accent } : undefined}
            aria-hidden="true"
          >
            <Icon size={15} />
          </span>
        )}
        <div className="flex min-w-0 flex-auto flex-col gap-[2px]">
          <h2
            id={headingId}
            className="m-0 [font-family:var(--font-sans)] text-[length:var(--text-lg)] font-semibold leading-[1.3] tracking-[-0.01em] text-[color:var(--chrome-fg)]"
          >
            {title}
          </h2>
          {description && (
            <p className="m-0 [font-family:var(--font-sans)] text-[length:var(--text-xs)] leading-[1.5] text-[color:var(--chrome-fg-dim)] [text-wrap:pretty]">
              {description}
            </p>
          )}
        </div>
        {actions && (
          <div
            data-slot="settings-section-actions"
            className="ml-[var(--space-4)] inline-flex shrink-0 flex-wrap items-center justify-end gap-[var(--space-3)] @max-[560px]/settings:ml-0 @max-[560px]/settings:w-full @max-[560px]/settings:justify-start"
          >
            {actions}
          </div>
        )}
      </header>
      <div className={contentClassName || undefined}>{children}</div>
    </section>
  );
}
