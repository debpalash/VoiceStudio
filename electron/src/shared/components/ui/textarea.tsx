import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * shadcn/ui Textarea — new-york style, themed through the VoiceStudio token bridge
 * (see index.css). Mirrors the Input shell (`border-input`/`bg-transparent`/
 * `placeholder:text-muted-foreground`/`focus-visible:ring-ring`/
 * `aria-invalid:border-destructive`) so multi-line fields match single-line
 * ones and recolor with every [data-theme]. `textareaBaseClass` is exported so
 * the VoiceStudio `Textarea` primitive (src/ui/Input.jsx) can layer its
 * padding-based size scale on top without re-deriving the shell.
 */
const textareaBaseClass =
  'border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 aria-invalid:border-destructive flex field-sizing-content min-h-16 w-full rounded-md border bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm';

function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return <textarea data-slot="textarea" className={cn(textareaBaseClass, className)} {...props} />;
}

export { Textarea, textareaBaseClass };
