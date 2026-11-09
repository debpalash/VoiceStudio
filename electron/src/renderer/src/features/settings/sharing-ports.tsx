import { NetworkIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiJson, describeError } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';

export interface PortInfo {
  backend_port: number;
  /** Null for the packaged app, whose UI is served from app:// rather than a port. */
  ui_port: number | null;
  share_port_base: number;
}

/** Settings → Sharing → Ports. Save is enabled only after the LAN port changes. */
export function SharingPorts({ sharing }: { sharing: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const ports = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<PortInfo>('/system/info', { signal }),
  });
  const [sharePort, setSharePort] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (ports.data && !sharePort) setSharePort(String(ports.data.share_port_base));
  }, [ports.data, sharePort]);

  if (!ports.data) return null;
  const saved = ports.data.share_port_base;
  const dirty = sharePort.trim() !== '' && Number(sharePort) !== saved;

  const save = async () => {
    const value = Number(sharePort);
    if (!Number.isInteger(value) || value < 1024 || value > 65535) {
      setError(t('sharing.port_error'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson('/system/set-env', {
        method: 'POST',
        body: JSON.stringify({ key: 'OMNIVOICE_SHARE_PORT', value: String(value) }),
      });
      client.setQueryData<PortInfo>(['system-info'], (current) =>
        current ? { ...current, share_port_base: value } : current,
      );
      toast.success(t('sharing.port_saved'));
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection icon={NetworkIcon} title={t('sharing.ports_title')}>
      <SettingsRow id="sharing-backend-port" title={t('sharing.backend_port')}>
        <code className="rounded-md bg-muted px-2 py-1 text-xs">{ports.data.backend_port}</code>
      </SettingsRow>
      {ports.data.ui_port !== null && (
        <SettingsRow id="sharing-ui-port" title={t('sharing.ui_port')}>
          <code className="rounded-md bg-muted px-2 py-1 text-xs">{ports.data.ui_port}</code>
        </SettingsRow>
      )}
      <SettingsRow
        id="sharing-share-port"
        title={t('sharing.lan_share_port')}
        description={t('sharing.ports_note')}
      >
        <Input
          type="number"
          min={1024}
          max={65535}
          value={sharePort}
          className="w-28 font-mono"
          aria-label={t('sharing.lan_share_port')}
          disabled={busy || sharing}
          onChange={(event) => {
            setSharePort(event.target.value);
            setError('');
          }}
        />
        <Button size="sm" disabled={busy || sharing || !dirty} onClick={() => void save()}>
          {busy ? t('common.saving') : t('common.save')}
        </Button>
      </SettingsRow>
      {error && (
        <p role="alert" className="px-4 py-3 text-sm text-destructive">
          {error}
        </p>
      )}
    </SettingsSection>
  );
}
