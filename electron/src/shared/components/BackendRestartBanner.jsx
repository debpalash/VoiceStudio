import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import { Loader, AlertTriangle, X } from 'lucide-react';
import { Button } from '../ui';

/**
 * BackendRestartBanner — the visible half of the #567 supervisor.
 *
 * The desktop shell auto-restarts a backend that dies mid-session and emits
 * `backend-restarting` / `backend-restored` / `backend-restart-failed` Tauri
 * events (src-tauri/src/bootstrap.rs promised a frontend banner for them —
 * this is it; until now nothing listened). Without it, a 10–20 s respawn
 * window was invisible: every in-flight request surfaced its own "Can't
 * reach the local VoiceStudio backend" toast with zero context, which read as
 * a recurring bug rather than a self-heal in progress.
 *
 * While restarting: a quiet pinned banner ("restarting — hang tight");
 * api/client.ts is simultaneously holding requests open via
 * backendLifecycleStage(), so most of the time the banner is ALL the user
 * sees before work resumes. Restored: brief success toast. Failed (restart
 * budget exhausted): a persistent danger banner — BackendCrashNotice and the
 * bootstrap splash own the deeper forensics.
 *
 * Outside the Tauri shell there are no lifecycle events; renders nothing.
 */
export default function BackendRestartBanner() {
  const { t } = useTranslation();
  const [state, setState] = useState(null); // null | 'restarting' | 'failed'

  useEffect(() => {
    if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return undefined;
    let alive = true;
    let unlisteners = [];
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlisteners = await Promise.all([
          listen('backend-restarting', () => {
            if (alive) setState('restarting');
          }),
          listen('backend-restored', () => {
            if (!alive) return;
            setState(null);
            toast.success(t('backend.restored', 'Voice backend is back — carrying on.'));
          }),
          listen('backend-restart-failed', () => {
            if (alive) setState('failed');
          }),
        ]);
        if (!alive) unlisteners.forEach((u) => u());
      } catch {
        /* shell events unavailable (web preview) — nothing to show */
      }
    })();
    return () => {
      alive = false;
      unlisteners.forEach((u) => u());
    };
  }, [t]);

  if (!state) return null;

  const failed = state === 'failed';
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed left-1/2 top-[var(--space-4)] z-[70] flex w-[min(600px,92vw)] -translate-x-1/2 items-center gap-[var(--space-3)] rounded-lg border border-border bg-bg-elev-1 px-[var(--space-4)] py-[var(--space-3)] shadow-lg backdrop-blur-md"
    >
      {failed ? (
        <AlertTriangle size={16} className="shrink-0 text-danger" aria-hidden />
      ) : (
        <Loader size={16} className="shrink-0 animate-spin text-warn" aria-hidden />
      )}
      <span className="flex-1 text-[length:var(--text-sm)] text-fg">
        {failed
          ? t(
              'backend.restart_failed',
              "The voice backend keeps crashing and couldn't be restarted — check the crash notice or Settings → Logs → Backend.",
            )
          : t(
              'backend.restarting',
              'The voice backend stopped and is restarting automatically — hang tight, this takes ~15 seconds.',
            )}
      </span>
      {failed && (
        <Button
          variant="ghost"
          size="sm"
          iconSize="sm"
          onClick={() => setState(null)}
          title={t('common.close', 'Close')}
        >
          <X size={12} />
        </Button>
      )}
    </div>
  );
}
