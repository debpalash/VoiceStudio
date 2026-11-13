import { isImeComposing } from '@/lib/ime';

/**
 * Ctrl/Cmd+Enter synthesizes (or queues, while a take renders). A held key
 * repeats nothing, and confirming an IME composition must not submit a
 * half-typed script.
 */
export function isSynthesizeShortcut(event: KeyboardEvent): boolean {
  return (
    (event.ctrlKey || event.metaKey) &&
    event.key === 'Enter' &&
    !event.repeat &&
    !isImeComposing(event)
  );
}
