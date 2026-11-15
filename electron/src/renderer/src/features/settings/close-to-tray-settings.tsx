import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getBridge } from '@/components/bridge';
import { Switch } from '@/components/ui/switch';
import { SettingsRow } from './settings-layout';

/**
 * Opt-in: the window's close button hides it to the system tray instead of
 * quitting, so running jobs survive an accidental close. Desktop only; the
 * browser UI has no window to hide. The main process owns the choice.
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
    setEnabled(next);
    app
      .setCloseToTray(next)
      .then(setEnabled)
      .catch(() => setEnabled(!next));
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
