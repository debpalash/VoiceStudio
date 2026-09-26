import { DictationDemo } from '@/components/dictation-demo';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { KeyboardIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import {
  dictationPreferencesKey,
  nativeShortcutKey,
  useDictationPreferences,
} from '@/hooks/use-native-dictation';
import {
  keyEventToAccelerator,
  isPureModifierEvent,
} from '@shared/utils/shortcutAccelerator';
import { SettingsSection, SettingsRow } from './settings-layout';

export function ShortcutSettings() {
  const { t } = useTranslation();
  const api = window.voicestudio?.capture;
  const client = useQueryClient();
  const prefs = useDictationPreferences();
  const shortcut = useQuery({
    queryKey: nativeShortcutKey,
    enabled: !!api,
    queryFn: () => api!.getShortcut(),
  });
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState('');
  const [rejected, setRejected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [verifiedShortcut, setVerifiedShortcut] = useState<string | null>(null);
  useEffect(() => api?.onShortcutPressed?.(setVerifiedShortcut), [api]);
  useEffect(() => {
    if (!recording) return;
    const cancel = () => {
      setRecording(false);
      setPending('');
      setRejected(false);
    };
    const key = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') {
        cancel();
        return;
      }
      const value = keyEventToAccelerator(event);
      if (!value) {
        if (!isPureModifierEvent(event)) setRejected(true);
        return;
      }
      setPending(value);
      setRejected(false);
      setRecording(false);
    };
    window.addEventListener('keydown', key, true);
    window.addEventListener('blur', cancel);
    return () => {
      window.removeEventListener('keydown', key, true);
      window.removeEventListener('blur', cancel);
    };
  }, [recording]);
  const save = async (value: string) => {
    if (!api) return;
    setBusy(true);
    setError(false);
    try {
      const saved = await api.setShortcut(value);
      client.setQueryData(nativeShortcutKey, saved);
      setPending('');
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const mode = async (value: 'hold' | 'toggle') => {
    if (!prefs.data) return;
    setBusy(true);
    setError(false);
    try {
      await apiJson('/dictation/prefs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: value }),
      });
      await api?.syncPreferences({ enabled: prefs.data.enabled, mode: value });
      await client.invalidateQueries({ queryKey: dictationPreferencesKey });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <DictationDemo />
      <SettingsSection icon={KeyboardIcon} title={t('settings.shortcut')}>
        <SettingsRow
          id="dictation-mode"
          title={t('voicePanel.mode_label')}
          description={t('voicePanel.mode_sub')}
        >
          {(['toggle', 'hold'] as const).map((value) => (
            <Button
              key={value}
              size="sm"
              variant={prefs.data?.mode === value ? 'secondary' : 'ghost'}
              aria-pressed={prefs.data?.mode === value}
              disabled={busy || !prefs.data}
              onClick={() => void mode(value)}
            >
              {t('voicePanel.mode_' + value)}
            </Button>
          ))}
        </SettingsRow>
        {api && (
          <>
            <SettingsRow id="dictation-shortcut" title={t('capture.active_shortcut')}>
              <kbd className="text-xs">{shortcut.data?.accelerator || t('common.loading')}</kbd>
              <span className="text-xs text-muted-foreground">
                {t(
                  !shortcut.data?.active
                    ? 'demo.dictation_status_warn'
                    : verifiedShortcut === shortcut.data.accelerator
                      ? 'demo.dictation_status_ok'
                      : 'demo.dictation_status_pending',
                )}
              </span>
            </SettingsRow>
            <div className="flex flex-wrap items-center gap-2 px-4 py-3">
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => {
                  setRecording(!recording);
                  setPending('');
                  setRejected(false);
                }}
              >
                <KeyboardIcon />
                {t(recording ? 'common.cancel' : 'capture.record_shortcut')}
              </Button>
              {pending && (
                <>
                  <kbd className="text-xs">{pending}</kbd>
                  <Button size="sm" disabled={busy} onClick={() => void save(pending)}>
                    {t('common.save')}
                  </Button>
                </>
              )}
              <Button
                variant="ghost"
                size="sm"
                disabled={busy || recording}
                onClick={() => void save('CmdOrCtrl+Shift+Space')}
              >
                {t('settings.shortcut_reset')}
              </Button>
              {recording && (
                <span role="status" className="text-xs text-muted-foreground">
                  {t(rejected ? 'capture.needs_modifier' : 'capture.press_key')}
                </span>
              )}
            </div>
            {(shortcut.data?.error || shortcut.isError || error) && (
              <div
                role="alert"
                className="flex items-center justify-between gap-3 px-4 py-3 text-sm text-destructive"
              >
                <span>{t('common.error')}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void save(shortcut.data?.accelerator || 'CmdOrCtrl+Shift+Space')}
                >
                  {t('common.retry')}
                </Button>
              </div>
            )}
          </>
        )}
        {!api && error && (
          <p role="alert" className="p-4 text-sm text-destructive">
            {t('common.error')}
          </p>
        )}
      </SettingsSection>
    </>
  );
}
