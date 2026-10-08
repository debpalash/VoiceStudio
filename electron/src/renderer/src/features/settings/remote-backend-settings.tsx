import { KeyRoundIcon, ServerCogIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getBridge } from '@/components/bridge';
import { SettingsRow, SettingsSection } from './settings-layout';

type Probe = Awaited<
  ReturnType<NonNullable<ReturnType<typeof getBridge>>['backend']['testRemote']>
>;

export function RemoteBackendSettings({
  reload = () => window.location.reload(),
}: {
  reload?: () => void;
}) {
  const { t } = useTranslation();
  const bridge = getBridge();
  const connection = useQuery({
    queryKey: ['backend-connection'],
    queryFn: () => bridge!.backend.getConnection(),
    enabled: Boolean(bridge),
    // An IPC read of the saved URL, not a network request. Query's default
    // `online` mode pauses it whenever the backend is down, which is exactly
    // when the gate shows this form to recover a failed remote.
    networkMode: 'always',
  });
  const dirty = useRef(false);
  const [url, setUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [probe, setProbe] = useState<Probe | null>(null);
  const [busy, setBusy] = useState<'test' | 'save' | 'local' | null>(null);

  useEffect(() => {
    if (!dirty.current && connection.data?.remote) setUrl(connection.data.url);
  }, [connection.data]);

  const input = () => ({ url: url.trim(), apiKey: apiKey.trim() });
  const test = async () => {
    if (!bridge || busy) return;
    setBusy('test');
    setProbe(null);
    const request = input();
    setApiKey('');
    try {
      setProbe(await bridge.backend.testRemote(request));
    } catch {
      setProbe({ ok: false, kind: 'network', target: request.url });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (!bridge || busy) return;
    setBusy('save');
    const request = input();
    setApiKey('');
    try {
      const result = await bridge.backend.useRemote(request);
      setProbe(result);
      if (result.ok) reload();
    } catch {
      setProbe({ ok: false, kind: 'network', target: request.url });
    } finally {
      setBusy(null);
    }
  };

  const useLocal = async () => {
    if (!bridge || busy) return;
    setBusy('local');
    try {
      await bridge.backend.useLocal();
      reload();
    } finally {
      setBusy(null);
    }
  };

  const probeText = probe?.ok
    ? t('settings.remote_backend_probe_ok', { detail: probe.detail })
    : probe?.kind === 'invalid'
      ? t('settings.remote_backend_invalid_url')
      : probe?.kind === 'auth'
        ? t('settings.remote_backend_error_http', { status: probe.status ?? 401 })
        : probe
          ? t(`settings.remote_backend_error_${probe.kind}`, { status: probe.status })
          : '';

  return (
    <SettingsSection icon={ServerCogIcon} title={t('settings.remote_backend_title')}>
      <SettingsRow
        id="remote-backend-url"
        title={t('settings.remote_backend_url')}
        description={t('settings.remote_backend_desc')}
      >
        <Input
          value={url}
          className="min-w-64 font-mono"
          aria-label={t('settings.remote_backend_url')}
          placeholder="http://gpu-box:3900"
          autoComplete="off"
          spellCheck={false}
          disabled={Boolean(busy)}
          onChange={(event) => {
            dirty.current = true;
            setUrl(event.target.value);
            setProbe(null);
          }}
        />
      </SettingsRow>
      <SettingsRow
        id="remote-backend-key"
        title={t('settings.remote_backend_key')}
        description={t('settings.remote_backend_key_placeholder')}
      >
        <KeyRoundIcon aria-hidden="true" className="size-4 text-muted-foreground" />
        <Input
          type="password"
          value={apiKey}
          className="min-w-64"
          aria-label={t('settings.remote_backend_key')}
          autoComplete="off"
          disabled={Boolean(busy)}
          onChange={(event) => {
            setApiKey(event.target.value);
            setProbe(null);
          }}
        />
      </SettingsRow>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <Button variant="outline" size="sm" disabled={Boolean(busy) || !url.trim()} onClick={test}>
          {t('settings.remote_backend_test')}
        </Button>
        <Button size="sm" disabled={Boolean(busy) || !url.trim()} onClick={save}>
          {t('settings.remote_backend_save')}
        </Button>
        {connection.data?.remote && (
          <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={useLocal}>
            {t('settings.remote_backend_use_local')}
          </Button>
        )}
        {probe && (
          <Badge
            role={probe.ok ? 'status' : 'alert'}
            variant={probe.ok ? 'outline' : 'destructive'}
            className={probe.ok ? 'text-emerald-500' : undefined}
          >
            {probeText}
          </Badge>
        )}
      </div>
    </SettingsSection>
  );
}
