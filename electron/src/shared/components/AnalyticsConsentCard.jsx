/**
 * AnalyticsConsentCard — the ONE place the app asks for analytics consent.
 *
 * Used by the first-run wizard (SetupWizard consent step) and by the one-time
 * banner existing installs see (AnalyticsConsentBanner). The rules it embodies:
 *
 *   - Two equal-weight buttons. Neither is visually "the default" — the app
 *     never nudges toward yes, and there is no pre-checked anything.
 *   - EITHER choice is persisted via PUT /api/settings/analytics, which also
 *     marks the user as prompted (backend: analytics.set_opted_in), so the
 *     question is asked exactly once.
 *   - Not answering (skipping the wizard, dismissing nothing) = not prompted =
 *     analytics stays OFF. Silence is not consent.
 *   - The copy is honest about what IS sent (engine names, durations, error
 *     types — content-free metadata behind an allowlist) and what NEVER is
 *     (your text, audio, files, identity). See backend/core/analytics.py for
 *     the enforcement that makes this true rather than aspirational.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiFetch } from '../api/client';
import { openExternal } from '../api/external';
import { enableAnalytics, disableAnalytics } from '../utils/analytics';
import { REPO_URL } from '../utils/bugReport';
import { Button } from '../ui';

export const ANALYTICS_FAQ_URL = `${REPO_URL}#-faq`;

/** Persist an explicit consent choice. Never throws — a failed write simply
 *  leaves the user unprompted, so the question can be asked again later. */
export async function chooseAnalyticsConsent(enabled) {
  try {
    await apiFetch('/api/settings/analytics', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    // Consent gates the FRONTEND SDK too: posthog-js is only initialised once
    // the user says yes, and torn down the moment they say no.
    if (enabled) await enableAnalytics();
    else disableAnalytics();
    return true;
  } catch (e) {
    console.warn('[analytics] consent choice failed (non-fatal)', e);
    return false;
  }
}

export default function AnalyticsConsentCard({ onDone, compact = false }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  const choose = async (enabled) => {
    if (busy) return;
    setBusy(true);
    try {
      const ok = await chooseAnalyticsConsent(enabled);
      onDone?.(enabled, ok);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={compact ? 'flex min-w-0 flex-col gap-2' : 'flex max-w-[62ch] flex-col gap-3'}>
      {!compact && (
        <h3 className="m-0 text-base font-semibold text-fg">
          {t('consent.title', 'Help improve VoiceStudio?')}
        </h3>
      )}
      <p className="m-0 text-sm leading-relaxed text-fg-muted">
        {t(
          'consent.body',
          'Anonymous, content-free usage stats: which engines are used, how long generations take, and the type of errors. Never your text, your audio, your files, or anything identifying you. Nothing is sent unless you say yes, and you can change your mind anytime in Settings → Privacy.',
        )}{' '}
        <button
          type="button"
          className="cursor-pointer appearance-none border-0 bg-transparent p-0 text-sm text-primary underline underline-offset-2 hover:opacity-80"
          onClick={() => openExternal(ANALYTICS_FAQ_URL).catch(() => {})}
        >
          {t('consent.learn_more', 'What exactly is sent?')}
        </button>
      </p>
      {/* Two equal-weight choices — same variant, same size, no default. */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="subtle"
          disabled={busy}
          onClick={() => choose(true)}
          data-testid="analytics-consent-yes"
        >
          {t('consent.yes', 'Yes, share anonymous stats')}
        </Button>
        <Button
          variant="subtle"
          disabled={busy}
          onClick={() => choose(false)}
          data-testid="analytics-consent-no"
        >
          {t('consent.no', 'No thanks')}
        </Button>
      </div>
    </div>
  );
}
