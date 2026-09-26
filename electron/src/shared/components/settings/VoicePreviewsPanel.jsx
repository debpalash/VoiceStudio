/**
 * Settings → Storage → Voice previews.
 *
 * One line and two controls for the pre-rendered voice gallery: a consent
 * toggle and a manual "Check now". The toggle is the *only* thing that ever
 * starts an outbound fetch — the backend downloads nothing until it flips, per
 * the local-first guarantee — and turning it on pulls the featured set so the
 * yes has a visible effect.
 *
 * The status line deliberately reads "Featured set cached", never "51 of 1126":
 * the catalog size is not a number anyone can act on. Freshness is rendered
 * with Intl.RelativeTimeFormat so "2 days ago" is correct in all 21 UI
 * languages without a phrase per unit.
 *
 * Endpoints:
 *   GET  /archetypes/previews/status  → {enabled, featured_cached, featured_total, …}
 *   PUT  /archetypes/previews         body {enabled}
 *   POST /archetypes/previews/check   → force a check, bypassing the 24 h throttle
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AudioLines, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';
import { Button } from '../../ui';

/** "2 days ago" in the active UI language, from a seconds-ago scalar. */
function formatChecked(seconds, language) {
  if (seconds == null) return null;
  const units = [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ];
  const rtf = new Intl.RelativeTimeFormat(language || 'en', { numeric: 'auto' });
  for (const [unit, size] of units) {
    if (seconds >= size) return rtf.format(-Math.floor(seconds / size), unit);
  }
  return rtf.format(0, 'minute');
}

export default function VoicePreviewsPanel() {
  const { t, i18n } = useTranslation();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setState(await apiJson('/archetypes/previews/status'));
    } catch (e) {
      setError(e?.message || null);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const send = useCallback(async (path, init) => {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(path, init);
      setState(await res.json());
    } catch (e) {
      setError(e?.message || null);
    } finally {
      setBusy(false);
    }
  }, []);

  const toggle = (enabled) =>
    send('/archetypes/previews', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });

  const enabled = Boolean(state?.enabled);
  const cached = state?.featured_cached ?? 0;
  const total = state?.featured_total ?? 0;
  const checked = formatChecked(state?.checked_seconds_ago, i18n.language);

  let line;
  if (!enabled) {
    line = t('models.voice_previews_off');
  } else if (total > 0 && cached >= total) {
    line = t('models.voice_previews_ready');
  } else {
    line = t('models.voice_previews_partial', { cached, total });
  }
  if (enabled) {
    line = `${line} · ${checked ? t('models.voice_previews_checked', { when: checked }) : t('models.voice_previews_never')}`;
  }

  return (
    <SettingsSection
      icon={AudioLines}
      title={t('models.voice_previews')}
      description={t('models.voice_previews_desc')}
    >
      <SettingRow
        title={t('models.voice_previews')}
        subtitle={line}
        control={
          <div className="inline-flex items-center gap-[var(--space-3)]">
            {enabled && (
              <Button
                size="sm"
                variant="subtle"
                disabled={busy}
                onClick={() => send('/archetypes/previews/check', { method: 'POST' })}
                data-testid="voice-previews-check"
              >
                <RefreshCw size={12} /> {t('models.voice_previews_check')}
              </Button>
            )}
            <SettingsToggle
              checked={enabled}
              disabled={busy}
              onChange={toggle}
              aria-label={t('models.voice_previews')}
            />
          </div>
        }
      />
      {(state?.last_error || error) && (
        <div
          className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-muted)]"
          data-testid="voice-previews-error"
        >
          {t('models.voice_previews_rejected', { message: state?.last_error || error })}
        </div>
      )}
    </SettingsSection>
  );
}
