import { useState } from 'react';
import { PictureInPicture2Icon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Switch } from '@/components/ui/switch';
import { setCompactOverlay, useCompactOverlay } from '@/lib/store/dictation-settings';
import { SettingsRow, SettingsSection } from './settings-layout';

/** Opt-in: press-and-hold dictation shows only a level meter and timer. */
export function CompactOverlaySettings() {
  const { t } = useTranslation();
  const enabled = useCompactOverlay();
  const [failed, setFailed] = useState(false);
  // The floating recorder only exists in the desktop app.
  if (!window.voicestudio?.capture) return null;
  return (
    <SettingsSection icon={PictureInPicture2Icon} title={t('dictation.compact_title')}>
      <SettingsRow
        id="dictation-compact-overlay"
        title={t('dictation.compact_row_title')}
        description={
          <>
            {t('dictation.compact_description')}
            {failed && (
              <span role="alert" className="mt-1 block text-destructive">
                {t('dictation.compact_save_failed')}
              </span>
            )}
          </>
        }
      >
        <Switch
          checked={enabled}
          aria-label={t('dictation.compact_row_title')}
          onCheckedChange={(value) => setFailed(!setCompactOverlay(value))}
        />
      </SettingsRow>
    </SettingsSection>
  );
}
