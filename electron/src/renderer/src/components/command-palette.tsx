import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { runRendererTask } from '@/lib/global-error-recovery';
import { isImeComposing } from '@/lib/ime';
import { LazyBoundary, retryableLazy } from '@/lib/lazy-boundary';

const CommandPaletteDialog = retryableLazy(
  () => import('./command-palette-dialog').then((m) => ({ default: m.CommandPaletteDialog })),
  null,
);

/** Closes the palette when its chunk cannot load, so the next open retries it. */
function CloseOnLoadFailure({ onClose }: { onClose: () => void }) {
  useEffect(onClose, [onClose]);
  return null;
}

/**
 * Lightweight keyboard/event listener mounted in AppShell.
 * Defers loading the dialog UI, icons, and history/profile queries until opened.
 */
export function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [openNonce, setOpenNonce] = useState(0);

  useEffect(() => {
    const show = () => {
      setOpenNonce((n) => n + 1);
      setOpen(true);
    };
    const key = (event: KeyboardEvent) => {
      if (isImeComposing(event) || event.repeat) return;
      // While the dialog chunk is still loading nothing else handles Escape, and the
      // dialog would pop open after the user dismissed it. Closing twice is harmless.
      if (event.key === 'Escape') {
        setOpen(false);
        setShortcutsOpen(false);
        return;
      }
      const target = event.target;
      const editing =
        target instanceof HTMLElement &&
        (target.matches('input, textarea') || target.isContentEditable);
      if (!editing && (event.key === '?' || (event.shiftKey && event.key === '/'))) {
        event.preventDefault();
        setOpen(false);
        setShortcutsOpen((value) => !value);
        return;
      }
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === 'k') {
        event.preventDefault();
        show();
      }
      if (event.key === ',') {
        event.preventDefault();
        runRendererTask('Keyboard navigation to settings', () => navigate({ to: '/settings' }));
      }
    };
    window.addEventListener('keydown', key);
    window.addEventListener('voicestudio:commands', show);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('voicestudio:commands', show);
    };
  }, [navigate]);

  const closeAll = useCallback(() => {
    setOpen(false);
    setShortcutsOpen(false);
  }, []);
  if (!open && !shortcutsOpen) return null;
  return (
    <LazyBoundary key={openNonce} fallback={<CloseOnLoadFailure onClose={closeAll} />}>
      <CommandPaletteDialog
        open={open}
        onOpenChange={setOpen}
        shortcutsOpen={shortcutsOpen}
        onShortcutsOpenChange={setShortcutsOpen}
        openNonce={openNonce}
      />
    </LazyBoundary>
  );
}
