import React from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle, CheckCircle, Keyboard, LockKeyhole, Mic, RotateCw } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Badge, Button } from '../../ui';
import { SettingsSection, SettingRow } from './primitives';
import usePermissions from '../../hooks/usePermissions';
import { detectPlatform, micHintKey } from '../../utils/micError';
import { openAccessibilitySettings, openMicrophoneSettings } from '../../utils/permissions';

/**
 * PermissionsPanel — Settings → Permissions: live OS-permission status for
 * the grants VoiceStudio's default features actually use, with a deep-link
 * into the exact OS settings pane when one is denied.
 *
 *   • Microphone — dictation + voice recording (all platforms; Linux has no
 *     per-app mic grant, so the status is honestly "Unknown" there).
 *   • Accessibility (macOS only) — lets dictation paste/type the transcript
 *     into other apps.
 *
 * Input Monitoring is deliberately NOT listed: the global dictation shortcut
 * registers via tauri-plugin-global-shortcut (Carbon hotkey registration on
 * macOS), which works without that grant.
 *
 * Status re-probes on window focus (usePermissions) — flip the toggle in
 * System Settings, come back, the chip is already green — plus an explicit
 * Recheck action. Outside the Tauri shell there is no OS state to read, so
 * the panel explains that instead of guessing.
 */

function StatusChip({ id, status, t }) {
  if (status === 'granted') {
    return (
      <Badge tone="success" data-testid={`perm-chip-${id}`}>
        <CheckCircle size={11} /> {t('permissions.status_granted')}
      </Badge>
    );
  }
  if (status === 'denied') {
    return (
      <Badge tone="warn" data-testid={`perm-chip-${id}`}>
        <AlertCircle size={11} /> {t('permissions.status_denied')}
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" data-testid={`perm-chip-${id}`}>
      {t(status === 'prompt' ? 'permissions.status_prompt' : 'permissions.status_unknown')}
    </Badge>
  );
}

export default function PermissionsPanel({ platform = detectPlatform() }) {
  const { t } = useTranslation();
  const { available, mic, a11y, recheck } = usePermissions();

  const openMicSettings = async () => {
    if (!(await openMicrophoneSettings())) {
      // Linux: no per-app mic-privacy pane exists — point at the system
      // sound settings instead of pretending the deep-link worked.
      toast(t('capture.mic_hint_linux'), { icon: 'ℹ️', duration: 8000 });
    }
  };

  return (
    <SettingsSection
      icon={LockKeyhole}
      title={t('permissions.title')}
      description={t('permissions.desc')}
      actions={
        available ? (
          <Button variant="ghost" size="sm" onClick={recheck} leading={<RotateCw size={12} />}>
            {t('setup.recheck')}
          </Button>
        ) : undefined
      }
    >
      {!available ? (
        <p className="m-0 font-sans text-[var(--text-sm)] leading-[1.6] text-[var(--chrome-fg-muted)]">
          {t('permissions.web_note')}
        </p>
      ) : (
        <>
          <SettingRow
            icon={Mic}
            title={t('permissions.microphone')}
            // Denied → the actionable per-OS path beats the generic "why".
            subtitle={mic === 'denied' ? t(micHintKey(platform)) : t('permissions.microphone_why')}
            control={
              <>
                <StatusChip id="microphone" status={mic} t={t} />
                {mic === 'denied' && (
                  <Button variant="ghost" size="sm" onClick={openMicSettings}>
                    {t('permissions.open_settings')}
                  </Button>
                )}
              </>
            }
          />
          {platform === 'mac' && (
            <SettingRow
              icon={Keyboard}
              title={t('permissions.accessibility')}
              subtitle={a11y ? t('permissions.accessibility_why') : t('capture.a11y_setup')}
              control={
                <>
                  <StatusChip id="accessibility" status={a11y ? 'granted' : 'denied'} t={t} />
                  {!a11y && (
                    <Button variant="ghost" size="sm" onClick={() => openAccessibilitySettings()}>
                      {t('permissions.open_settings')}
                    </Button>
                  )}
                </>
              }
            />
          )}
        </>
      )}
    </SettingsSection>
  );
}
