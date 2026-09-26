import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button, Dialog } from '../ui';
import { openBugReport } from '../utils/bugReport';
import { detectHints, isUnrecoverableFailure } from './BootstrapSplash';

/**
 * BackendStartFailureNotice — the surfacing half of #1177.
 *
 * The desktop shell always knows WHY a backend start failed:
 * `BootstrapStage::Failed { message }` (src-tauri/src/bootstrap.rs) carries the
 * exit code plus a ~30-line stderr tail, or the precise reason the venv
 * bootstrap refused (Intel Mac unsupported, a failed `uv sync`, a blocked
 * GitHub). BootstrapSplash renders that diagnosis — but only while the splash
 * is up. Once the app is running, a later start failure (a supervisor giving up
 * after a crash loop, a Retry that re-failed) had no surface at all: apiFetch
 * threw the evidence-free "Can't reach the local VoiceStudio backend — it may
 * still be starting up, or it stopped", which is the exact string #1177
 * reported, and which is also false — it is not starting, and it will not
 * recover on its own.
 *
 * This is the splash's failure card, after the splash: the shell's own words,
 * the same actionable `bootstrap.hint_*` matcher (shared via `detectHints`, so
 * the two can never drift), and the report affordance that attaches the
 * evidence. api/client.ts dispatches `ov:backend-start-failed` with an
 * already-scrubbed message.
 *
 * Outside the Tauri shell there is no shell to fail this way — the event is
 * never dispatched (the lifecycle stage is 'unknown'), so this renders nothing
 * and browser/Docker keep their own deployment-specific message (#1164).
 */
export default function BackendStartFailureNotice() {
  const { t } = useTranslation();
  const [message, setMessage] = useState(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    const onFailed = (e) => {
      const m = e?.detail?.message;
      if (typeof m === 'string' && m.trim()) setMessage(m.trim());
    };
    window.addEventListener('ov:backend-start-failed', onFailed);
    return () => window.removeEventListener('ov:backend-start-failed', onFailed);
  }, []);

  const dismiss = useCallback(() => {
    setShowDetails(false);
    setMessage(null);
  }, []);

  if (!message) return null;

  // Same matcher the splash uses, so a start failure reads identically before
  // and after the splash. No logs here (the splash's log buffer is gone by
  // now) — the shell's message is the whole evidence.
  const hints = detectHints(message, []);
  const unrecoverable = isUnrecoverableFailure(message, []);

  return (
    <>
      <div
        role="alert"
        /* Matches BackendCrashNotice's placement: clear of the ~2rem navbar and
           above its stacking level, so the banner and its actions stay
           clickable. */
        className="fixed left-1/2 top-[calc(var(--space-4)_+_2.25rem)] z-[110] flex w-[min(600px,92vw)] -translate-x-1/2 items-center gap-[var(--space-3)] rounded-lg border border-border bg-bg-elev-1 px-[var(--space-4)] py-[var(--space-3)] shadow-lg backdrop-blur-md"
      >
        <AlertTriangle size={16} className="shrink-0 text-danger" aria-hidden />
        <span className="flex-1 text-[length:var(--text-sm)] text-fg">
          {t('backend_start_failure.notice')}
        </span>
        <Button variant="subtle" size="sm" onClick={() => setShowDetails(true)}>
          {t('backend_start_failure.view')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          iconSize="sm"
          onClick={dismiss}
          title={t('crash.dismiss')}
        >
          <X size={12} />
        </Button>
      </div>

      <Dialog
        open={showDetails}
        onClose={() => setShowDetails(false)}
        title={t('backend_start_failure.title')}
        size="lg"
        footer={
          <>
            <Button
              variant="subtle"
              onClick={async () => {
                try {
                  // The message is already scrubbed by api/client.ts;
                  // buildBugReportUrl scrubs the Error text again and attaches
                  // the environment block, so the report arrives WITH the
                  // evidence and WITHOUT the user's home path.
                  await openBugReport({
                    title: '[Backend] Backend failed to start',
                    error: new Error(message),
                  });
                } catch (e) {
                  // Never fail silently: the user clicked Report and must be
                  // told it didn't open, plus the fallback that still works
                  // (the diagnosis is on screen above, ready to copy).
                  console.warn('[BackendStartFailureNotice] report action failed', e);
                  toast.error(t('errors.report_failed'));
                }
              }}
            >
              {t('errors.report')}
            </Button>
            <Button variant="primary" onClick={dismiss}>
              {t('common.close')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-[var(--space-4)]">
          <p className="m-0 text-[length:var(--text-sm)] text-fg-muted">
            {t('backend_start_failure.intro')}
          </p>
          <div>
            <div className="mb-[var(--space-2)] text-[length:var(--text-sm)] text-fg-subtle">
              {t('backend_start_failure.output_title')}
            </div>
            <pre className="m-0 max-h-[40vh] overflow-auto rounded-md border border-border bg-bg-elev-2 p-[var(--space-3)] font-mono text-[length:var(--text-xs)] leading-relaxed text-fg whitespace-pre-wrap">
              {message}
            </pre>
          </div>
          <ul className="m-0 flex list-disc flex-col gap-[var(--space-2)] pl-[var(--space-5)] text-[length:var(--text-sm)] text-fg-muted">
            {hints.map((key) => (
              <li key={key}>{t(key)}</li>
            ))}
            {/* An unrecoverable failure (Intel Mac) must not send the user to a
                Retry that can never work — #1112's dead end. */}
            {!unrecoverable && <li>{t('backend_start_failure.retry_hint')}</li>}
          </ul>
        </div>
      </Dialog>
    </>
  );
}
