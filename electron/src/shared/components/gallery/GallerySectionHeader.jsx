/**
 * GallerySectionHeader — one quiet section divider for every gallery zone.
 *
 * Mono kicker + optional count pill + a hairline that fades to the right
 * (the Launchpad collection pattern): structure from spacing and type, never
 * from boxes. `icon` tints the kicker; `count` renders the pill.
 */
export default function GallerySectionHeader({ icon = null, title, count = null }) {
  return (
    <div className="gallery-section-header m-0 mb-[10px] flex shrink-0 items-center gap-[9px] [font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] font-medium uppercase [letter-spacing:var(--chrome-label-track)] text-[color:var(--chrome-fg-dim)] after:h-px after:flex-1 after:content-[''] after:[background-image:linear-gradient(90deg,color-mix(in_srgb,var(--chrome-fg)_14%,transparent),transparent)]">
      {icon}
      <span className="min-w-0 truncate">{title}</span>
      {count !== null && count !== undefined && (
        <span
          className="shrink-0 rounded-full bg-[var(--chrome-hover-bg)] px-[8px] py-[1px] normal-case tracking-normal text-[color:var(--chrome-fg-muted)] [font-variant-numeric:tabular-nums]"
          data-testid="gallery-section-count"
        >
          {count}
        </span>
      )}
    </div>
  );
}
