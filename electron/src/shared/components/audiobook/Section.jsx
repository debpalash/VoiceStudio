import { ChevronDown } from 'lucide-react';

/**
 * Collapsible settings group for the Audiobook tab — one consistent affordance
 * for the secondary controls (Output, Book details, Pronunciation, Markup) so
 * the right-hand column stops sprawling. Native `<details>/<summary>` gives
 * keyboard operability + a labelled disclosure for free; the chrome mirrors the
 * AudiobookOverrides header (small muted mono label + a chevron that rotates
 * when open) so every group header reads as one family.
 *
 * `defaultOpen` sets the initial state only (uncontrolled, like the overrides
 * panel) — the user's toggle is theirs to keep for the session.
 */
export default function Section({ title, icon = null, defaultOpen = false, children }) {
  return (
    <details className="audiobook-tab__section group flex flex-col gap-[8px]" open={defaultOpen}>
      <summary className="flex items-center gap-[6px] px-[4px] py-[4px] cursor-pointer select-none list-none [&::-webkit-details-marker]:hidden [font-family:var(--chrome-font-mono)] [font-size:var(--chrome-label-size)] font-semibold [letter-spacing:var(--chrome-label-track)] uppercase [color:var(--chrome-fg-muted)] hover:[color:var(--chrome-fg)]">
        {icon}
        <span>{title}</span>
        <ChevronDown
          size={12}
          className="ml-auto transition-transform group-open:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <div className="flex flex-col gap-[8px]">{children}</div>
    </details>
  );
}
