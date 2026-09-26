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
import { Button, Badge } from '../../ui';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import RestartBadge from './RestartBadge';

export default function NetworkTab() {
  const { t } = useTranslation();
  const { data: sysInfo } = useSystemInfo();
  const [proxyUrl, setProxyUrl] = useState('');
  const [proxySaved, setProxySaved] = useState(false);
  const [proxyCleared, setProxyCleared] = useState(false);
  const [proxySaving, setProxySaving] = useState(false);
  const queryClient = useQueryClient();
  const openSettingsTab = useAppStore((s) => s.openSettingsTab);

  useEffect(() => {
    if (!proxyUrl && !proxySaved && !proxyCleared) setProxyUrl(sysInfo?.proxy_url || '');
  }, [sysInfo?.proxy_url]);

  const ffmpegOk = sysInfo?.ffmpeg_ok;
  // "A proxy is configured" must survive an app reload: derive it from the
  // backend-persisted value, not only from a save in this session — otherwise
  // the Clear button (and the "Set" badge) vanish on reload with the proxy
  // still active and no way to remove it.
  const proxyConfigured = !proxyCleared && (proxySaved || Boolean(sysInfo?.proxy_url));

  const saveProxy = async () => {
    const value = proxyUrl.trim();
    setProxySaving(true);
    try {
      const { apiFetch } = await import('../../api/client');
      const setEnv = (key, val) =>
        apiFetch('/system/set-env', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, value: val }),
        });
      await setEnv('HTTP_PROXY', value);
      await Promise.all([
        setEnv('HTTPS_PROXY', value),
        setEnv('ALL_PROXY', value),
        setEnv('http_proxy', value),
        setEnv('https_proxy', value),
        setEnv('all_proxy', value),
      ]);
      toast.success(t('settings.proxy_saved'));
      setProxySaved(true);
      setProxyCleared(false);
      queryClient.invalidateQueries({ queryKey: queryKeys.systemInfo });
    } catch (e) {
      toast.error(t('settings.save_failed', { message: e.message }));
    } finally {
      setProxySaving(false);
    }
  };

  const clearProxy = async () => {
    setProxySaving(true);
    try {
      const { apiFetch } = await import('../../api/client');
      const setEnv = (key, val) =>
        apiFetch('/system/set-env', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, value: val }),
        });
      await Promise.all([
        setEnv('HTTP_PROXY', ''),
        setEnv('HTTPS_PROXY', ''),
        setEnv('ALL_PROXY', ''),
        setEnv('http_proxy', ''),
        setEnv('https_proxy', ''),
        setEnv('all_proxy', ''),
      ]);
      setProxyUrl('');
      setProxySaved(false);
      setProxyCleared(true);
      toast.success(t('settings.proxy_cleared'));
      queryClient.invalidateQueries({ queryKey: queryKeys.systemInfo });
    } catch (e) {
      toast.error(t('settings.clear_failed', { message: e.message }));
    } finally {
      setProxySaving(false);
    }
  };

  return (
    <SettingsSection
      icon={Wifi}
      title={t('settings.network', { defaultValue: 'Network' })}
      description={t('settings.network_desc', {
        defaultValue: 'Proxy for downloads and model fetches.',
      })}
    >
      <SettingRow
        align="start"
        stack
        icon={Globe}
        title={
          <>
            {t('settings.proxy')}
            <RestartBadge applies />
            {proxyConfigured && (
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
              placeholder="http://127.0.0.1:7890 or socks5://127.0.0.1:7890"
              value={proxyUrl}
              onChange={(e) => setProxyUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && saveProxy()}
              aria-label={t('settings.proxy_input_aria', { defaultValue: 'Proxy URL' })}
            />
            <Button
              size="sm"
              variant="subtle"
              onClick={saveProxy}
              loading={proxySaving}
              disabled={!proxyUrl.trim()}
            >
              {t('credentials.save')}
            </Button>
            {proxyConfigured && (
              <Button
                size="sm"
                variant="ghost"
                onClick={clearProxy}
                loading={proxySaving}
                data-testid="proxy-clear"
              >
                {t('settings.proxy_clear')}
              </Button>
            )}
          </>
        }
      />

      {/* Pointer, not a control — the FFmpeg override lives in Audio tools now.
          Two competing writers of env.FFMPEG_PATH would fight each other. */}
      <SettingRow
        icon={Film}
        title={
          <>
            {t('settings.ffmpeg')}
            <Badge tone={ffmpegOk ? 'success' : 'warn'} size="xs">
              {ffmpegOk ? t('settings.ffmpeg_found') : t('settings.ffmpeg_missing')}
            </Badge>
          </>
        }
        note={t('settings.audio_tools_moved_note', {
          defaultValue:
            'The FFmpeg override moved to its own panel with more control (version, origin, restore).',
        })}
        control={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => openSettingsTab('audio-tools')}
            data-testid="open-audio-tools"
          >
            {t('settings.audio_tools_open', { defaultValue: 'Open Audio tools' })} →
          </Button>
        }
      />
    </SettingsSection>
  );
}
