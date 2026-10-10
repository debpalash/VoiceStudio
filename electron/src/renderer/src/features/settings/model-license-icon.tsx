import { cn } from '@/lib/utils';
import type { ModelLicenseCategory } from './model-license-contract';

const handle = <path d="M8 7V5.5A1.5 1.5 0 0 1 9.5 4h5A1.5 1.5 0 0 1 16 5.5V7" />;
// Two-tone: a light tint of the category colour behind the outline.
const tint = { fill: 'currentColor', fillOpacity: 0.18 } as const;
const tintedBody = <rect x="3" y="7" width="18" height="13" rx="2" {...tint} />;

/**
 * Declared-licence category glyphs, drawn to stay legible at 16px. One briefcase
 * family (business use) plus a distinct outline for unknown, drawn two-tone in
 * the category colour. Colour reinforces the shape; adjacent or accessible
 * text always carries the meaning.
 */
const glyphs: Record<ModelLicenseCategory, React.ReactNode> = {
  commercial: (
    <>
      {tintedBody}
      {handle}
      <path d="M3 12.5h18" />
    </>
  ),
  conditions: (
    <>
      {tintedBody}
      {handle}
      <path d="M12 10.5v4M12 17.5h.01" />
    </>
  ),
  noncommercial: (
    <>
      <path
        d="M5 7a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2z"
        {...tint}
        stroke="none"
      />
      <path d="M7 7H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14M21 16.5V9a2 2 0 0 0-2-2h-7.5" />
      {handle}
      <path d="M3 3l18 18" />
    </>
  ),
  unknown: (
    <>
      <circle cx="12" cy="12" r="9" {...tint} />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.4M12 17h.01" />
    </>
  ),
};

const tones: Record<ModelLicenseCategory, string> = {
  commercial: 'text-success',
  conditions: 'text-warning',
  noncommercial: 'text-destructive',
  unknown: 'text-muted-foreground',
};

export function ModelLicenseIcon({
  category,
  className = 'size-4',
}: {
  category: ModelLicenseCategory;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn(tones[category], className)}
      data-license-category={category}
      aria-hidden="true"
      focusable="false"
    >
      {glyphs[category]}
    </svg>
  );
}
