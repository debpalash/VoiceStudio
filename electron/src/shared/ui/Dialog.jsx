import React from 'react';
import { X } from 'lucide-react';
import {
  Dialog as ShadcnDialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from '@/components/ui/dialog';
import Button from './Button';

// Per-size max-width. The dialog surface (glass gradient + backdrop-filter) and
// the blurred overlay live in Dialog.css, keyed off shadcn's data-slot
// attributes; centering + open/close animation come from shadcn's DialogContent
// (Radix data-[state] + tw-animate-css). The header/title/body/footer box-model
// stays as utilities below.
const DIALOG_MAX_W = {
  sm: 'max-w-[380px]',
  md: 'max-w-[560px]',
  lg: 'max-w-[780px]',
  xl: 'max-w-[1080px]',
};

/**
 * Dialog — accessible modal backed by shadcn/ui Dialog (which wraps
 * @radix-ui/react-dialog).
 *
 * Provides focus trapping, Escape-to-close, scroll lock, and
 * proper ARIA attributes out of the box.
 *
 * @param open        controlled visibility
 * @param onClose     called on backdrop click / ESC / close button
 * @param title       string | ReactNode in the header; omit for header-less dialog
 * @param footer      node rendered in the footer region (actions)
 * @param size        'sm' | 'md' | 'lg' | 'xl'
 * @param dismissable whether backdrop click / ESC closes (default true)
 */
export default function Dialog({
  open,
  onClose,
  title = null,
  footer = null,
  size = 'md',
  dismissable = true,
  children,
}) {
  const handleOpenChange = (nextOpen) => {
    if (!nextOpen && dismissable) onClose?.();
  };

  const handleEscapeKeyDown = (e) => {
    if (!dismissable) e.preventDefault();
  };

  const handlePointerDownOutside = (e) => {
    if (!dismissable) e.preventDefault();
  };

  return (
    <ShadcnDialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={`ui-dialog flex flex-col gap-0 overflow-hidden p-0 ${DIALOG_MAX_W[size] || DIALOG_MAX_W.md}`}
        onEscapeKeyDown={handleEscapeKeyDown}
        onPointerDownOutside={handlePointerDownOutside}
        aria-describedby={undefined}
      >
        {(title || dismissable) && (
          <header className="flex shrink-0 items-center justify-between gap-[var(--space-4)] border-b border-border px-[var(--space-6)] py-[var(--space-5)]">
            {title && (
              <DialogTitle className="m-0 font-serif text-[length:var(--text-lg)] font-bold tracking-[-0.01em] text-fg">
                {title}
              </DialogTitle>
            )}
            {dismissable && (
              <DialogClose asChild>
                <Button variant="icon" iconSize="sm" aria-label="Close">
                  <X size={12} />
                </Button>
              </DialogClose>
            )}
          </header>
        )}
        {!title && <DialogTitle className="sr-only">Dialog</DialogTitle>}
        <div className="min-h-0 overflow-y-auto p-[var(--space-6)]">{children}</div>
        {footer && (
          <footer className="flex shrink-0 items-center justify-end gap-[var(--space-3)] border-t border-border px-[var(--space-6)] py-[var(--space-4)]">
            {footer}
          </footer>
        )}
      </DialogContent>
    </ShadcnDialog>
  );
}
