/**
 * Settings → Privacy → "Help improve VoiceStudio" — the opt-in analytics control.
 *
 * Local-first means **silence is not consent**: this is OFF until the user turns
 * it on, so a default install still transmits nothing. The panel tells the truth
 * in the UI rather than burying it in a policy nobody opens:
 *
 *   - exactly what IS sent (counts, durations, which engine, error TYPE),
 *   - exactly what is NEVER sent (the text you type, your audio, filenames,
 *     voice names, and any identity),
 *   - and that it can be turned off again at any time.
 *
 * When the build ships no analytics destination (rare since #1193 — the in-repo
 * default token covers source builds too), the toggle is not offered at all —
 * an inert switch would be a lie. See
 * backend/core/analytics.py for the enforcement (allowlist + no exception
 * autocapture) that makes the promises above true rather than aspirational.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import { Check, X } from 'lucide-react';
import { apiJson, apiFetch } from '../../api/client';
import { enableAnalytics, disableAnalytics } from '../../utils/analytics';
import { SettingRow, SettingsToggle } from './primitives';

export default function AnalyticsOptIn() {
  const { t } = useTranslation();
  const [state, setState] = useState(null); // { enabled, opted_in, available }
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    apiJson('/api/settings/analytics')
      .then((d) => alive && setState(d))
      .catch(() => {
        /* backend down — just don't render the control */
      });
    return () => {
      alive = false;
    };
  }, []);

  const toggle = async (next) => {
    setBusy(true);
    try {
      const d = await apiFetch('/api/settings/analytics', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      }).then((r) => r.json());
      setState(d);
      // Consent gates the FRONTEND SDK too: posthog-js is only initialised once
      // the user says yes, and torn down the moment they say no. It is never
      // started at app load — that would track people before they consented.
      if (next) await enableAnalytics();
      else disableAnalytics();
      toast.success(
        next
          ? t('privacy.analytics_on', { defaultValue: 'Thanks — anonymous usage stats are on.' })
          : t('privacy.analytics_off', { defaultValue: 'Analytics off. Nothing is sent.' }),
      );
    } catch (e) {
      toast.error(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  // No destination in this build (e.g. running from source) → an inert toggle
  // would be dishonest. Say nothing rather than offer a switch that does nothing.
  if (!state?.available) return null;

  const Item = ({ ok, children }) => (
    <li className="flex items-start gap-[var(--space-2)]">
      {ok ? (
        <Check size={13} className="mt-[3px] shrink-0 text-[var(--chrome-accent)]" />
      ) : (
        <X size={13} className="mt-[3px] shrink-0 text-[var(--chrome-fg-muted)]" />
      )}
      <span>{children}</span>
    </li>
  );

  return (
    <>
      <SettingRow
        title={t('privacy.analytics_title', { defaultValue: 'Help improve VoiceStudio' })}
        subtitle={t('privacy.analytics_subtitle', {
          defaultValue: 'Off by default. Anonymous usage stats — never your content.',
        })}
        control={
          <SettingsToggle
            checked={!!state.opted_in}
            disabled={busy}
            onChange={toggle}
            aria-label={t('privacy.analytics_title', { defaultValue: 'Help improve VoiceStudio' })}
            data-testid="analytics-optin"
          />
        }
      />
      <ul className="m-0 mb-[var(--space-4)] list-none p-0 [font-family:var(--font-sans)] text-[length:var(--text-sm)] leading-[1.7] text-[var(--chrome-fg-muted)]">
        <Item ok>
          {t('privacy.analytics_sends', {
            defaultValue:
              'Sent: which engine and language you used, how long a generation took, how many characters (a number, not the text), and the type of any error.',
          })}
        </Item>
        <Item>
          {t('privacy.analytics_never', {
            defaultValue:
              'Never sent: the text you type, your audio, your file names, your voice names, or anything identifying you. Not your name, not your email, not your IP.',
          })}
        </Item>
        <Item>
          {t('privacy.analytics_off_anytime', {
            defaultValue: 'You can turn this off again at any time, and nothing further is sent.',
          })}
        </Item>
      </ul>
    </>
  );
}
