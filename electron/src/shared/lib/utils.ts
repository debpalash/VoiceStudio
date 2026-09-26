import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * cn — merge conditional class lists, then resolve Tailwind utility conflicts.
 *
 * `clsx` flattens the conditional/array/object class inputs; `twMerge` then
 * de-dupes conflicting Tailwind utilities so a caller-supplied `className`
 * (passed last) wins over the component's defaults — the canonical shadcn/ui
 * helper. Used by the shadcn primitives in `src/components/ui/*`.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
