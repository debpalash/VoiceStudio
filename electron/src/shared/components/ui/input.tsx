import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * shadcn/ui Input — new-york style, themed through the VoiceStudio token bridge
 * (see index.css). `border-input`/`text-foreground`/`placeholder:text-muted-
 * foreground`/`focus-visible:ring-ring`/`aria-invalid:border-destructive`
 * resolve to the VoiceStudio palette, so it shows the chrome border, muted
 * placeholder, and brand-pink focus ring, recoloring with every [data-theme].
 * Proof component for the foundation — not yet wired into the app (the existing
 * src/ui/Input.jsx stays the live primitive; see docs/shadcn-migration.md).
 */
/**
 * inputBaseClass — the shadcn/ui Input shell: chrome border, transparent fill,
 * muted placeholder, brand focus ring, destructive invalid state, all resolved
 * through the VoiceStudio token bridge so it tracks every [data-theme].
 *
 * Exported so the VoiceStudio form primitives (`src/ui/Input.jsx`) reuse this
 * exact shell on their native <select> — which must stay a real <select> for
 * the `onChange={(e) => …e.target.value}` call sites and therefore can't render
 * through this <input> component — keeping Input/Select/Textarea visually
 * coherent. The fixed `h-9` lives here; the wrappers override it with their own
 * padding-based size scale.
 */
const inputBaseClass =
  'file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground border-input flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 aria-invalid:border-destructive';

function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <input type={type} data-slot="input" className={cn(inputBaseClass, className)} {...props} />
  );
}

export { Input, inputBaseClass };
