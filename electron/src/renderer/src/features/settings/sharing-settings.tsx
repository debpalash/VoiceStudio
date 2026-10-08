import QRCode from 'qrcode';
import {
  CheckIcon,
  CopyIcon,
  ExternalLinkIcon,
  Globe2Icon,
  LoaderCircleIcon,
  NetworkIcon,
  ShieldCheckIcon,
  WifiIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getBridge } from '@/components/bridge';
import { apiJson, describeError } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';
import { McpBindingsSettings } from './mcp-bindings-settings';
import { RemoteBackendSettings } from './remote-backend-settings';

interface NetworkState {
  enabled: boolean;
  share_port: number | null;
  pin: string | null;
  lan_addresses: string[];
}

interface PortInfo {
  backend_port: number;
  /** Null for the packaged app, whose UI is served from app:// rather than a port. */
  ui_port: number | null;
  share_port_base: number;
}

interface TailscaleStatus {
  installed: boolean;
  running: boolean;
}

interface TailscaleResult {
  ok: boolean;
  url?: string;
  note?: string;
  error?: string;
}

const TAILSCALE_DOWNLOAD_URL = 'https://tailscale.com/download';

function post<T>(path: string, body?: unknown): Promise<T> {
  return apiJson<T>(path, {
    method: 'POST',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

function ExternalAction({ url, label }: { url: string; label: string }) {
  return (
    <Button
      type="button"
      size="icon-sm"
      variant="ghost"
      aria-label={label}
      title={label}
      onClick={() => {
        const bridge = getBridge();
        if (bridge) {
          try {
            void Promise.resolve(bridge.files.openExternal(url)).catch((error) =>
              toast.error(describeError(error)),
            );
          } catch (error) {
            toast.error(describeError(error));
          }
        } else window.open(url, '_blank', 'noopener,noreferrer');
      }}
    >
      <ExternalLinkIcon aria-hidden="true" />
    </Button>
  );
}

export function SharingSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const network = useQuery({
    queryKey: ['network-share'],
    queryFn: ({ signal }) => apiJson<NetworkState>('/system/network/state', { signal }),
  });
  const ports = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<PortInfo>('/system/info', { signal }),
  });
  const tailscale = useQuery({
    queryKey: ['tailscale-status'],
    queryFn: ({ signal }) => apiJson<TailscaleStatus>('/system/tailscale/status', { signal }),
  });
  const [confirming, setConfirming] = useState(false);
  const [networkBusy, setNetworkBusy] = useState(false);
  const [networkError, setNetworkError] = useState('');
  const [sharePort, setSharePort] = useState('');
  const [portBusy, setPortBusy] = useState(false);
  const [portError, setPortError] = useState('');
  const [tailscaleBusy, setTailscaleBusy] = useState(false);
  const [tailscaleError, setTailscaleError] = useState('');
  const [tailscaleUrl, setTailscaleUrl] = useState('');
  const [tailscaleNote, setTailscaleNote] = useState('');
  const [qrCodes, setQrCodes] = useState<Record<string, string>>({});

  useEffect(() => {
    if (ports.data && !sharePort) setSharePort(String(ports.data.share_port_base));
  }, [ports.data, sharePort]);

  useEffect(() => {
    const state = network.data;
    if (!state?.enabled || !state.pin || !state.share_port) {
      setQrCodes({});
      return;
    }
    let active = true;
    void Promise.all(
      state.lan_addresses.map(
        async (address) =>
          [
            address,
            await QRCode.toDataURL(`http://${address}:${state.share_port}/?pin=${state.pin}`),
          ] as const,
      ),
    )
      .then((entries) => {
        if (active) setQrCodes(Object.fromEntries(entries));
      })
      .catch(() => {
        if (active) setQrCodes({});
      });
    return () => {
      active = false;
    };
  }, [network.data]);

  const setNetworkState = (state: NetworkState) => {
    client.setQueryData(['network-share'], state);
    setConfirming(false);
  };

  const enableNetwork = async () => {
    if (networkBusy) return;
    setNetworkBusy(true);
    setNetworkError('');
    try {
      setNetworkState(await post<NetworkState>('/system/network/enable'));
    } catch (error) {
      setNetworkError(describeError(error));
    } finally {
      setNetworkBusy(false);
    }
  };

  const disableNetwork = async () => {
    if (networkBusy) return;
    setNetworkBusy(true);
    setNetworkError('');
    try {
      await post('/system/network/disable');
      setNetworkState({ enabled: false, share_port: null, pin: null, lan_addresses: [] });
    } catch (error) {
      setNetworkError(describeError(error));
    } finally {
      setNetworkBusy(false);
    }
  };

  const savePort = async () => {
    const value = Number(sharePort);
    if (!Number.isInteger(value) || value < 1024 || value > 65535) {
      setPortError(t('sharing.port_error'));
      return;
    }
    setPortBusy(true);
    setPortError('');
    try {
      await post('/system/set-env', { key: 'OMNIVOICE_SHARE_PORT', value: String(value) });
      client.setQueryData<PortInfo>(['system-info'], (current) =>
        current ? { ...current, share_port_base: value } : current,
      );
      toast.success(t('sharing.port_saved'));
    } catch (error) {
      setPortError(describeError(error));
    } finally {
      setPortBusy(false);
    }
  };

  const enableTailscale = async () => {
    if (tailscaleBusy) return;
    setTailscaleBusy(true);
    setTailscaleError('');
    try {
      const result = await post<TailscaleResult>('/system/tailscale/enable');
      if (!result.ok) throw new Error(result.error || t('sharing.tailscale_enable_failed'));
      setTailscaleUrl(result.url || '');
      setTailscaleNote(result.note || '');
      await tailscale.refetch();
    } catch (error) {
      setTailscaleError(describeError(error));
    } finally {
      setTailscaleBusy(false);
    }
  };

  const disableTailscale = async () => {
    if (tailscaleBusy) return;
    setTailscaleBusy(true);
    setTailscaleError('');
    try {
      const result = await post<TailscaleResult>('/system/tailscale/disable');
      if (result?.ok === false)
        throw new Error(result.error || t('sharing.tailscale_disable_failed'));
      setTailscaleUrl('');
      setTailscaleNote('');
      await tailscale.refetch();
    } catch (error) {
      setTailscaleError(describeError(error));
    } finally {
      setTailscaleBusy(false);
    }
  };

  const copyLink = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t('network.copied'));
    } catch (error) {
      setNetworkError(describeError(error));
    }
  };

  const state = network.data;
  return (
    <>
      <RemoteBackendSettings />
      <SettingsSection icon={WifiIcon} title={t('sharing.title')}>
        <SettingsRow
          id="sharing-lan"
          title={t('sharing.local_network')}
          description={t('sharing.local_help')}
        >
          {state?.enabled ? (
            <>
              <Badge variant="outline" className="text-emerald-500">
                <CheckIcon aria-hidden="true" />
                {t('network.network')}
              </Badge>
              <Button variant="outline" size="sm" disabled={networkBusy} onClick={disableNetwork}>
                {networkBusy ? t('network.switching') : t('network.stop_sharing')}
              </Button>
            </>
          ) : confirming ? (
            <div className="max-w-md space-y-2 text-left">
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t('network.share_confirm_hint')}
              </p>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                  {t('common.cancel')}
                </Button>
                <Button size="sm" disabled={networkBusy} onClick={enableNetwork}>
                  {networkBusy ? t('network.enabling') : t('network.enable')}
                </Button>
              </div>
            </div>
          ) : (
            <Button size="sm" disabled={network.isPending} onClick={() => setConfirming(true)}>
              <WifiIcon aria-hidden="true" />
              {t('network.share_on_network')}
            </Button>
          )}
        </SettingsRow>
        {state?.enabled && (
          <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
            {state.lan_addresses.length === 0 && (
              <p className="text-sm text-muted-foreground">{t('network.no_interface')}</p>
            )}
            {state.lan_addresses.map((address) => {
              const url = `http://${address}:${state.share_port}/?pin=${state.pin}`;
              return (
                <article
                  key={address}
                  className="rounded-xl border border-border/60 bg-background/40 p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <code className="min-w-0 truncate text-xs">
                      {address}:{state.share_port}
                    </code>
                    <div className="flex shrink-0">
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        aria-label={t('network.copy_link')}
                        title={t('network.copy_link')}
                        onClick={() => void copyLink(url)}
                      >
                        <CopyIcon aria-hidden="true" />
                      </Button>
                      <ExternalAction url={url} label={t('network.open_in_browser')} />
                    </div>
                  </div>
                  {qrCodes[address] && (
                    <img
                      src={qrCodes[address]}
                      alt={t('network.qr_alt', { ip: address })}
                      className="mx-auto mt-3 size-28 rounded-lg bg-white p-1"
                    />
                  )}
                </article>
              );
            })}
            {state.pin && (
              <div className="flex min-h-28 flex-col items-center justify-center rounded-xl border border-border/60 bg-primary/5 p-3 text-center">
                <ShieldCheckIcon aria-hidden="true" className="mb-2 size-5 text-primary" />
                <span className="text-xs text-muted-foreground">{t('network.pin')}</span>
                <strong className="font-mono text-xl tracking-[0.18em]">{state.pin}</strong>
              </div>
            )}
          </div>
        )}
        {(network.isError || networkError) && (
          <p role="alert" className="px-4 py-3 text-sm text-destructive">
            {networkError || t('common.error')}
          </p>
        )}
      </SettingsSection>

      {ports.data && (
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
              disabled={portBusy || state?.enabled}
              onChange={(event) => {
                setSharePort(event.target.value);
                setPortError('');
              }}
            />
            <Button size="sm" disabled={portBusy || state?.enabled} onClick={() => void savePort()}>
              {portBusy ? t('common.saving') : t('common.save')}
            </Button>
          </SettingsRow>
          {portError && (
            <p role="alert" className="px-4 py-3 text-sm text-destructive">
              {portError}
            </p>
          )}
        </SettingsSection>
      )}

      <SettingsSection icon={Globe2Icon} title={t('sharing.tailscale_title')}>
        <SettingsRow
          id="sharing-tailscale"
          title={
            tailscale.data?.installed
              ? tailscale.data.running
                ? t('sharing.tailscale_running')
                : t('sharing.tailscale_not_logged_in')
              : t('sharing.tailscale_absent')
          }
          description={t('sharing.help')}
        >
          {tailscale.isPending ? (
            <LoaderCircleIcon
              className="size-4 animate-spin text-muted-foreground"
              aria-label={t('common.loading')}
            />
          ) : tailscale.data?.installed ? (
            tailscaleUrl ? (
              <Button
                variant="outline"
                size="sm"
                disabled={tailscaleBusy}
                onClick={disableTailscale}
              >
                {t('sharing.tailscale_disable_btn')}
              </Button>
            ) : (
              <Button size="sm" disabled={tailscaleBusy} onClick={enableTailscale}>
                {t('sharing.tailscale_enable_btn')}
              </Button>
            )
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                const bridge = getBridge();
                if (bridge)
                  void bridge.files
                    .openExternal(TAILSCALE_DOWNLOAD_URL)
                    .catch((error) => toast.error(describeError(error)));
                else window.open(TAILSCALE_DOWNLOAD_URL, '_blank', 'noopener,noreferrer');
              }}
            >
              <ExternalLinkIcon aria-hidden="true" />
              {t('sharing.tailscale_install')}
            </Button>
          )}
        </SettingsRow>
        {tailscaleUrl && (
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            <code className="min-w-0 flex-1 break-all rounded-md bg-muted px-2 py-1 text-xs">
              {tailscaleUrl}
            </code>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={t('sharing.tailscale_copy')}
              onClick={() => void copyLink(tailscaleUrl)}
            >
              <CopyIcon aria-hidden="true" />
            </Button>
            <ExternalAction url={tailscaleUrl} label={t('sharing.tailscale_open')} />
            {tailscaleNote && (
              <p className="w-full text-xs text-muted-foreground">{tailscaleNote}</p>
            )}
          </div>
        )}
        {(tailscale.isError || tailscaleError) && (
          <p role="alert" className="px-4 py-3 text-sm text-destructive">
            {tailscaleError || t('common.error')}
          </p>
        )}
      </SettingsSection>
      <McpBindingsSettings />
    </>
  );
}
