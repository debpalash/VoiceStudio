/**
 * Settings → Network.
 *
 * Proxy only. The FFmpeg-path override that used to share this panel moved to
 * Settings → Audio tools (same backend store — prefs `env.FFMPEG_PATH` via
 * `/media-tools` — richer controls: version, origin, restore bundled); a
 * pointer row below deep-links there so muscle memory still lands.
 */
import React, { useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { Wifi, Globe, Film } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '../../store';
import { useSystemInfo, queryKeys } from '../../api/hooks';
import { apiFetch } from '../../api/client';
import { Button, Badge } from '../../ui';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import RestartBadge from './RestartBadge';

const PROXY_KEYS = [
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY',
  'http_proxy',
  'https_proxy',
  'all_proxy',
];

export default function NetworkTab() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const systemInfo = useSystemInfo();
  const openSettingsTab = useAppStore((state) => state.openSettingsTab);
  const [proxy, setProxy] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [cleared, setCleared] = useState(false);
  const backendProxy = systemInfo.data?.proxy_url || '';

  useEffect(() => {
    if (!saved && !cleared) setProxy((current) => current || backendProxy);
  }, [backendProxy, cleared, saved]);

  const writeProxy = async (value) => {
    await Promise.all(
      PROXY_KEYS.map((key) =>
        apiFetch('/system/set-env', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, value }),
        }),
      ),
    );
  };

  const saveProxy = async () => {
    const value = proxy.trim();
    setBusy(true);
    try {
      await writeProxy(value);
      setSaved(true);
      setCleared(false);
      toast.success(t('settings.proxy_saved'));
      void queryClient.invalidateQueries({ queryKey: queryKeys.systemInfo });
    } catch (error) {
      toast.error(t('settings.save_failed', { message: error.message }));
    } finally {
      setBusy(false);
    }
  };

  const clearProxy = async () => {
    setBusy(true);
    try {
      await writeProxy('');
      setProxy('');
      setSaved(false);
      setCleared(true);
      toast.success(t('settings.proxy_cleared'));
      void queryClient.invalidateQueries({ queryKey: queryKeys.systemInfo });
    } catch (error) {
      toast.error(t('settings.clear_failed', { message: error.message }));
    } finally {
      setBusy(false);
    }
  };

  const configured = !cleared && (saved || Boolean(backendProxy));

  return (
    <SettingsSection
      icon={Wifi}
      title={t('settings.network')}
      description={t('settings.network_desc')}
    >
      <SettingRow
        icon={Globe}
        align="start"
        stack
        title={
          <>
            {t('settings.proxy')}
            <RestartBadge applies />
            {configured && (
              <Badge tone="success" size="xs">
                {t('credentials.saved')}
              </Badge>
            )}
          </>
        }
        note={t('settings.proxy_desc')}
        control={
          <>
            <SettingsInput
              value={proxy}
              onChange={(event) => setProxy(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void saveProxy();
              }}
              placeholder="http://127.0.0.1:8080 or socks5://127.0.0.1:1080"
              aria-label={t('settings.proxy_input_aria')}
              disabled={busy}
            />
            <Button
              size="sm"
              variant="subtle"
              disabled={!proxy.trim()}
              loading={busy}
              onClick={() => void saveProxy()}
            >
              {t('credentials.save')}
            </Button>
            {configured && (
              <Button
                size="sm"
                variant="ghost"
                loading={busy}
                data-testid="proxy-clear"
                onClick={() => void clearProxy()}
              >
                {t('settings.proxy_clear')}
              </Button>
            )}
          </>
        }
      />
      <SettingRow
        icon={Film}
        title={
          <>
            {t('settings.ffmpeg')}
            <Badge tone={systemInfo.data?.ffmpeg_ok ? 'success' : 'warn'} size="xs">
              {t(systemInfo.data?.ffmpeg_ok ? 'settings.ffmpeg_found' : 'settings.ffmpeg_missing')}
            </Badge>
          </>
        }
        note={t('settings.audio_tools_moved_note')}
        control={
          <Button
            size="sm"
            variant="ghost"
            data-testid="open-audio-tools"
            onClick={() => openSettingsTab('audio-tools')}
          >
            {t('settings.audio_tools_open')} →
          </Button>
        }
      />
    </SettingsSection>
  );
}
