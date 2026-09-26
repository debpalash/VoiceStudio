/**
 * Settings → Privacy → "Invisible watermark" — the control the app already
 * promised.
 *
 * `errors.a_watermark` told users "Commercial licensees can disable it in
 * Settings → Privacy". That control did not exist: the backend endpoints
 * (`/watermark/status`, `/watermark/settings`) had zero callers in the
 * frontend, `is_enabled()` passes no `env=` to `resolve()` so there was no
 * environment escape either, and the only way off was hand-editing prefs.json.
 * A shipped instruction that cannot be followed is worse than no instruction.
 *
 * Deliberately mirrors AnalyticsOptIn's shape but inverts its default: analytics
 * is OFF until you opt in, provenance marking is ON until you opt out. Both are
 * honest about which way they point rather than hiding behind a policy page.
 *
 * When AudioSeal is unavailable on this install the toggle is not offered — an
 * inert switch claiming to control a mark that cannot be embedded would be the
 * same lie in the other direction.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import { getWatermarkStatus, setWatermarkSettings } from '../../api/watermark';
import { SettingRow, SettingsToggle } from './primitives';

export default function WatermarkControl() {
  const { t } = useTranslation();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  // A toggle that resolves after the tab closes must not set state or toast at
  // whatever screen the user moved on to (CodeRabbit).
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );

  // One retry after a short delay. A single shot meant that opening Privacy
  // while the backend was restarting hid the control for the rest of the
  // session — and the control's whole purpose is being findable, since the FAQ
  // tells people it is here (Greptile P1).
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const load = (attempt = 0) => {
      getWatermarkStatus()
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch(() => {
          if (!cancelled && attempt === 0) timer = setTimeout(() => load(1), 2000);
          // Second failure: render nothing rather than an inert control.
        });
    };

    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (!status || !status.audioseal_available) return null;

  const toggle = async () => {
    const next = !status.invisible_enabled;
    setBusy(true);
    try {
      const updated = await setWatermarkSettings({ invisible_enabled: next });
      if (!alive.current) return;
      setStatus((prev) => ({ ...prev, ...updated }));
      toast.success(
        next
          ? t('privacy.watermark_on_toast', {
              defaultValue: 'Invisible watermark enabled for new audio.',
            })
          : t('privacy.watermark_off_toast', {
              defaultValue: 'Invisible watermark disabled for new audio.',
            }),
      );
    } catch {
      if (!alive.current) return;
      toast.error(
        t('privacy.watermark_failed', {
          defaultValue: 'Could not change the watermark setting.',
        }),
      );
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <SettingRow
      title={t('privacy.watermark_title', { defaultValue: 'Invisible watermark' })}
      subtitle={t('privacy.watermark_subtitle', {
        defaultValue:
          'On by default. Marks generated audio as AI-made so it can be identified later. Only affects audio generated from now on.',
      })}
      control={
        <SettingsToggle
          checked={!!status.invisible_enabled}
          disabled={busy}
          onChange={toggle}
          aria-label={t('privacy.watermark_title', { defaultValue: 'Invisible watermark' })}
          data-testid="watermark-toggle"
        />
      }
    />
  );
}
