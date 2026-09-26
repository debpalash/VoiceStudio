/**
 * AnalyticsConsentBanner — the one-time consent ask for EXISTING installs.
 *
 * New installs are asked in the first-run wizard (SetupWizard consent step),
 * but that wizard never reruns for installs that predate the prompt. So on app
 * start, if the user has never been asked (`prompted` false) and analytics is
 * off, this shows a single dismissible banner with the same equal-weight
 * Yes/No choice (same pattern as BackendCrashNotice's top banner).
 *
 * One-shot semantics:
 *   - Yes / No  → persisted; `prompted` set; never shown again.
 *   - Dismiss (X) → treated as No (analytics stays/goes off, `prompted` set);
 *     never shown again. Dismissal is a choice, not a snooze — nagging users
 *     into consent would be its own kind of dark pattern.
 *   - Backend unreachable / destination-less build / already prompted /
 *     already opted in → renders nothing. (Source builds have a destination
 *     since #1193, so they get this same one-time ask.)
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BarChart3, X } from 'lucide-react';
import { apiJson } from '../api/client';
import AnalyticsConsentCard, { chooseAnalyticsConsent } from './AnalyticsConsentCard';
import { Button } from '../ui';

export default function AnalyticsConsentBanner() {
  const { t } = useTranslation();
  const [show, setShow] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiJson('/api/settings/analytics')
      .then((s) => {
        // Ask only when the build CAN send (token baked in), the user was
        // never asked, and analytics is off. Anything else — including a
        // backend error — means no banner. Silence is not consent, and an
        // unanswerable question would be noise.
        if (!cancelled && s?.available && !s?.prompted && !s?.opted_in) setShow(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!show) return null;

  const dismiss = () => {
    setShow(false);
    // Dismiss = an explicit "no": persists OFF and marks prompted, so the
    // banner never reappears. Fire-and-forget — a failed write only means
    // the user may be asked again next launch.
    chooseAnalyticsConsent(false);
  };

  return (
    <div
      role="dialog"
      aria-label={t('consent.title', 'Help improve VoiceStudio?')}
      className="fixed left-1/2 top-[var(--space-4)] z-[70] flex w-[min(680px,92vw)] -translate-x-1/2 items-start gap-[var(--space-3)] rounded-lg border border-border bg-bg-elev-1 px-[var(--space-4)] py-[var(--space-3)] shadow-lg backdrop-blur-md"
      data-testid="analytics-consent-banner"
    >
      <BarChart3 size={16} className="mt-[3px] shrink-0 text-primary" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="mb-1 text-[length:var(--text-sm)] font-semibold text-fg">
          {t('consent.title', 'Help improve VoiceStudio?')}
        </div>
        <AnalyticsConsentCard compact onDone={() => setShow(false)} />
      </div>
      <Button
        variant="ghost"
        size="sm"
        iconSize="sm"
        onClick={dismiss}
        title={t('consent.dismiss', 'Dismiss (keeps analytics off)')}
      >
        <X size={12} />
      </Button>
    </div>
  );
}
