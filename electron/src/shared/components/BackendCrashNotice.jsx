import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button, Dialog } from '../ui';
import {
  acknowledgeBackendCrash,
  crashAge,
  crashCauseHint,
  describeCrashExit,
  getUnacknowledgedBackendCrash,
  hasCrashEvidence,
  isSentinelMarker,
} from '../utils/backendCrash';
import { openBugReport } from '../utils/bugReport';

/**
 * BackendCrashNotice — the honest half of #941.
 *
 * When the backend PROCESS dies, the desktop shell records a crash marker
 * (src-tauri/src/crash.rs). This component surfaces it: a banner naming the
 * exit code and when it happened, with a "View crash details" affordance that
 * shows the captured stderr tail and a report path. Sources:
 *   - `ov:backend-crashed` window events, dispatched by api/client.ts when a
 *     request fails against a freshly crashed backend, and
 *   - a mount-time check, so a crash that happened with no request in flight
 *     (or a crash-loop that forced an app restart) still gets told.
 *
 * Viewing or dismissing acknowledges the marker (it is retained on disk so
 * bug reports can still attach the evidence). Outside the Tauri shell the
 * marker getters resolve null and this renders nothing.
 */
export default function BackendCrashNotice() {
  const { t } = useTranslation();
  const [marker, setMarker] = useState(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getUnacknowledgedBackendCrash()
      .then((m) => {
        if (!cancelled && m) setMarker(m);
      })
      .catch(() => {});
    const onCrash = (e) => {
      if (e?.detail) setMarker(e.detail);
    };
    window.addEventListener('ov:backend-crashed', onCrash);
    return () => {
      cancelled = true;
      window.removeEventListener('ov:backend-crashed', onCrash);
    };
  }, []);

  const view = useCallback(() => {
    setShowDetails(true);
    // Ack on view — the user has seen the honest story; the marker itself
    // stays on disk for bug-report attachment.
    acknowledgeBackendCrash().catch(() => {});
  }, []);

  const dismiss = useCallback(() => {
    acknowledgeBackendCrash().catch(() => {});
    setShowDetails(false);
    setMarker(null);
  }, []);

  if (!marker) return null;

  const exit = describeCrashExit(marker);
  const ago = crashAge(marker);
  // The run sentinel does not know whether the previous run crashed — sleep,
  // force-quit, a stopped VM and a Docker restart leave the same trace, and
  // those benign causes are the majority. Say so instead of "crashed" (#1375).
  const sentinel = isSentinelMarker(marker);
  // ...and a report whose evidence block would be EMPTY is not offered as one
  // click: it files an issue nobody can answer. The user is pointed at the
  // backend log instead; a real crash (exit code, signal, or a captured tail)
  // keeps the one-click path.
  const reportable = hasCrashEvidence(marker);
  // What the exit code and the captured tail actually mean, and what to do
  // about it (#1927). The classifier already knew — a native fault points at a
  // GPU driver that disagrees with the bundled CUDA runtime, exit 78 at a port
  // conflict, an import traceback at a half-built environment — but until now
  // the only surface that showed it was a dropped stream. A user who opened
  // this dialog after "Backend died (exit code -1073741819)" got a raw number,
  // a timestamp and a log they cannot read, with nothing to try. A sentinel
  // marker is excluded on purpose: it does not know a crash happened at all,
  // so it has no cause to explain.
  const cause = sentinel ? '' : crashCauseHint(marker);

  return (
    <>
      <div
        role="alert"
        /* Sits BELOW the top navbar (.header-area is ~2rem tall and z-index:100).
           Anchoring at top-[var(--space-4)] with z-[70] tucked the banner — and
           its "View crash details" button — UNDER the navbar, which paints over
           it. Clear the header height and rise above its stacking level so the
           whole alert (and its actions) is always visible and clickable. */
        className="fixed left-1/2 top-[calc(var(--space-4)_+_2.25rem)] z-[110] flex w-[min(600px,92vw)] -translate-x-1/2 items-center gap-[var(--space-3)] rounded-lg border border-border bg-bg-elev-1 px-[var(--space-4)] py-[var(--space-3)] shadow-lg backdrop-blur-md"
      >
        <AlertTriangle size={16} className="shrink-0 text-danger" aria-hidden />
        <span className="flex-1 text-[length:var(--text-sm)] text-fg">
          {sentinel ? t('crash.notice_unclean', { ago }) : t('crash.notice', { exit, ago })}
        </span>
        <Button variant="subtle" size="sm" onClick={view}>
          {t('crash.view')}
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
        onClose={() => {
          setShowDetails(false);
          setMarker(null);
        }}
        title={t('crash.details_title')}
        size="lg"
        footer={
          <>
            {reportable && (
              <Button
                variant="subtle"
                onClick={async () => {
                  try {
                    // buildBugReportUrl attaches the crash marker (exit code +
                    // scrubbed stderr tail) automatically — the report arrives
                    // WITH the evidence.
                    // A sentinel report must not claim a crash in its title —
                    // the marker's whole point is that it cannot know
                    // (CodeRabbit on #1380). The evidence still rides along.
                    await openBugReport({
                      title: sentinel
                        ? '[Crash] Backend ended uncleanly (previous run)'
                        : `[Crash] Backend died (${exit})`,
                    });
                  } catch (e) {
                    // Same class as BackendStartFailureNotice (#1177): a Report
                    // click that silently does nothing reads as a broken button.
                    console.warn('[BackendCrashNotice] report action failed', e);
                    toast.error(t('errors.report_failed'));
                  }
                }}
              >
                {t('errors.report')}
              </Button>
            )}
            <Button variant="primary" onClick={dismiss}>
              {t('common.close')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-[var(--space-4)]">
          <p className="m-0 text-[length:var(--text-sm)] text-fg-muted">
            {sentinel
              ? t('crash.details_intro_unclean', { ago })
              : t('crash.details_intro', { exit, ago })}
          </p>
          {cause && <p className="m-0 text-[length:var(--text-sm)] text-fg">{cause}</p>}
          {!reportable && (
            <p className="m-0 text-[length:var(--text-sm)] text-fg-muted">
              {t('crash.report_needs_log')}
            </p>
          )}
          <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-[var(--space-5)] gap-y-[var(--space-2)] text-[length:var(--text-sm)]">
            <dt className="text-fg-subtle">{t('crash.field_exit')}</dt>
            <dd className="m-0 font-mono text-fg">{exit}</dd>
            <dt className="text-fg-subtle">{t('crash.field_when')}</dt>
            <dd className="m-0 text-fg">{new Date(marker.ts * 1000).toLocaleString()}</dd>
            <dt className="text-fg-subtle">{t('crash.field_uptime')}</dt>
            <dd className="m-0 text-fg">{t('crash.uptime_value', { count: marker.uptime_s })}</dd>
            <dt className="text-fg-subtle">{t('crash.field_version')}</dt>
            <dd className="m-0 text-fg">{marker.backend_version}</dd>
          </dl>
          <div>
            <div className="mb-[var(--space-2)] text-[length:var(--text-sm)] text-fg-subtle">
              {t('crash.stderr_title')}
            </div>
            <pre className="m-0 max-h-[40vh] overflow-auto rounded-md border border-border bg-bg-elev-2 p-[var(--space-3)] font-mono text-[length:var(--text-xs)] leading-relaxed text-fg whitespace-pre-wrap">
              {marker.last_stderr || t('crash.no_stderr')}
            </pre>
          </div>
        </div>
      </Dialog>
    </>
  );
}
