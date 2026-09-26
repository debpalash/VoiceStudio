/**
 * Settings → Capture → Echo cancellation panel (parity program Action 8).
 *
 * Opt-in toggle for dictate-over-playback AEC. When on, dictation streams raw
 * PCM through the backend's server-side NLMS canceller (`/ws/transcribe?aec=1`)
 * and the audio player taps its output as the echo reference, so dictating
 * while VoiceStudio plays audio doesn't transcribe the playback. Off by default —
 * dictation uses the standard MediaRecorder path and behaves identically on
 * every platform. The pref is the zustand `aecEnabled` flag (persisted); no
 * backend round-trip needed. All strings go through i18n (`dictation.aec_*`).
 */
import React from 'react';
import { Volume2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../../store';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';

export default function AecPanel() {
  const { t } = useTranslation();
  const aecEnabled = useAppStore((s) => s.aecEnabled);
  const setAecEnabled = useAppStore((s) => s.setAecEnabled);

  return (
    <SettingsSection
      icon={Volume2}
      title={t('dictation.aec_title')}
      description={t('dictation.aec_description')}
    >
      <SettingRow
        title={t('dictation.aec_row_title')}
        subtitle={t('dictation.aec_experimental')}
        hint={t('dictation.aec_hint')}
        control={
          <SettingsToggle
            checked={aecEnabled}
            onChange={setAecEnabled}
            aria-label={t('dictation.aec_row_title')}
          />
        }
      />
    </SettingsSection>
  );
}
