import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { getBridge } from '@/components/bridge';
import { Switch } from '@/components/ui/switch';
import { SettingsRow } from './settings-layout';

/**
 * Opt-in: the window's close button hides it to the system tray instead of
 * quitting, so running jobs survive an accidental close. Desktop only; the
 * browser UI has no window to hide. The main process owns the choice and only
 * confirms it once it is saved.
 */
export function CloseToTraySettings() {
  const { t } = useTranslation();
  const app = getBridge()?.app;
  const supported = typeof app?.getCloseToTray === 'function';
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    const bridge = getBridge()?.app;
    if (typeof bridge?.getCloseToTray !== 'function') return;
    let live = true;
    bridge
      .getCloseToTray()
      .then((value) => live && setEnabled(value))
      .catch(() => live && setEnabled(null));
    return () => {
      live = false;
    };
  }, []);

  if (!app || !supported || enabled === null) return null;

  const change = (next: boolean) => {
    const confirmed = enabled;
    setEnabled(next);
    app
      .setCloseToTray(next)
      .then(setEnabled)
      .catch(() => {
        // The main process could not save it: keep the last confirmed value.
        setEnabled(confirmed);
        toast.error(t('settings.close_to_tray_failed'));
      });
  };

  return (
    <SettingsRow
      id="close-to-tray-label"
      title={t('settings.close_to_tray')}
      description={t('settings.close_to_tray_desc')}
    >
      <Switch aria-labelledby="close-to-tray-label" checked={enabled} onCheckedChange={change} />
    </SettingsRow>
  );
}
