import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AudioLinesIcon,
  FileTextIcon,
  FolderIcon,
  KeyboardIcon,
  LanguagesIcon,
  LaptopIcon,
  LockIcon,
  MicIcon,
  MonitorIcon,
  SlidersHorizontalIcon,
  StarIcon,
  type LucideIcon,
} from 'lucide-react';
import { ExternalLink } from '@/components/external-link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { formatRelative } from '@/features/clone/format';
import { apiJson } from '@/lib/api/client';
import { cn } from '@/lib/utils';

type ChoiceKey = 'settings' | 'shortcuts' | 'favorites';
const CHOICES: ChoiceKey[] = ['settings', 'shortcuts', 'favorites'];
/** Later collections, shown locked or paused as the server lists them (guide §3). */
const LATER: Record<string, string> = {
  voices: 'cloudsync.voices',
  takes: 'cloudsync.takes',
  pronunciation: 'cloudsync.projects',
  'dub-projects': 'cloudsync.dub_projects',
  transcriptions: 'cloudsync.dictation_history',
};
const ICONS: Record<string, LucideIcon> = {
  settings: SlidersHorizontalIcon,
  shortcuts: KeyboardIcon,
  favorites: StarIcon,
  voices: AudioLinesIcon,
  takes: MicIcon,
  pronunciation: FolderIcon,
  'dub-projects': LanguagesIcon,
  transcriptions: FileTextIcon,
};

interface Collection {
  name: string;
  entitled: boolean;
  enabled: boolean;
}
interface SyncView {
  enabled: boolean;
  device_id: string | null;
  choices: Record<ChoiceKey, boolean>;
  collections: Collection[];
  usage: {
    used_bytes?: number;
    limit_bytes?: number;
    collections?: Array<{ name: string; items: number; bytes: number }>;
  };
  other_devices: Array<{ device_id: string; choices: Record<string, unknown> }>;
  status: {
    conflicts: Array<{ collection: string; item_id: string; keys: string[] }>;
    needs_update: string[];
    paused: Record<string, string>;
    storage_full: boolean;
    offline: boolean;
    stopped: string | null;
    pending_mass_delete: { count: number } | null;
  };
}
interface Device {
  id: string;
  name: string;
  platform: string;
  os_version?: string;
  current?: boolean;
  last_seen_at?: string;
}

const STATUS_KEY = ['cloud-sync-status'];

function size(bytes = 0): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** One dense row of the sync lists: icon, name, inline status, and its control. */
function SyncRow({
  id,
  icon: Icon,
  name,
  badge,
  meta,
  muted = false,
  children,
}: {
  id: string;
  icon: LucideIcon;
  name: ReactNode;
  badge?: ReactNode;
  meta?: ReactNode;
  muted?: boolean;
  children?: ReactNode;
}) {
  return (
    <li data-slot="sync-row" className="flex min-h-11 items-center gap-3 py-1.5">
      <Icon
        aria-hidden="true"
        className={cn(
          'size-4 shrink-0',
          muted ? 'text-muted-foreground/60' : 'text-muted-foreground',
        )}
      />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
        <span id={id} className={cn('min-w-0 truncate text-sm', muted && 'text-muted-foreground')}>
          {name}
        </span>
        {badge}
        {meta ? (
          <span className="min-w-0 truncate text-xs text-muted-foreground">{meta}</span>
        ) : null}
      </div>
      {children ? <div className="flex shrink-0 items-center gap-2">{children}</div> : null}
    </li>
  );
}

function SyncGroup({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id}>
      <h3 id={id} className="text-xs font-medium text-muted-foreground">
        {title}
      </h3>
      <ul className="mt-1 divide-y divide-border/40">{children}</ul>
    </section>
  );
}

const CALLOUT = 'rounded-lg border border-border/50 bg-muted/20 px-3 py-2 text-sm';

/**
 * Settings → Account → Sync: what this device syncs, its devices, and what
 * went wrong. Rendered inside the account card, so it carries the card's inset.
 */
export function CloudSyncPanel({ planUrl }: { planUrl?: string | null } = {}) {
  const { t, i18n } = useTranslation();
  const locale = i18n?.resolvedLanguage || i18n?.language || undefined;
  const client = useQueryClient();
  const status = useQuery({
    queryKey: STATUS_KEY,
    queryFn: ({ signal }) => apiJson<SyncView>('/api/cloud-sync/status', { signal }),
    refetchInterval: 5000,
  });
  const view = status.data;
  const devices = useQuery({
    queryKey: ['cloud-sync-devices'],
    queryFn: ({ signal }) => apiJson<{ devices: Device[] }>('/api/cloud-sync/devices', { signal }),
    enabled: Boolean(view?.enabled && !view.status.stopped),
  });
  const [draft, setDraft] = useState<Record<ChoiceKey, boolean>>({
    settings: true,
    shortcuts: false,
    favorites: false,
  });
  const [turningOff, setTurningOff] = useState<ChoiceKey | null>(null);
  const [failed, setFailed] = useState(false);

  const act = async (path: string, body?: unknown, method = 'POST') => {
    setFailed(false);
    try {
      const next = await apiJson<SyncView>(path, {
        method,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (next && 'enabled' in next) client.setQueryData(STATUS_KEY, next);
      await client.invalidateQueries({ queryKey: STATUS_KEY });
      await client.invalidateQueries({ queryKey: ['cloud-sync-devices'] });
    } catch {
      setFailed(true);
    }
  };

  const error = failed ? (
    <p role="alert" className="text-sm text-destructive">
      {t('common.error')}
    </p>
  ) : null;

  if (status.isError)
    return (
      <div className="px-4 py-3">
        <p role="status" className="text-sm text-muted-foreground">
          {t('cloudsync.unavailable')}
        </p>
      </div>
    );
  if (!view) return null;

  if (!view.enabled)
    return (
      <div data-slot="cloud-sync-panel" className="space-y-3 px-4 py-3">
        <SyncGroup id="sync-choose-heading" title={t('cloudsync.choose')}>
          {CHOICES.map((key) => (
            <SyncRow key={key} id={`sync-${key}`} icon={ICONS[key]} name={t(`cloudsync.${key}`)}>
              <Switch
                aria-label={t(`cloudsync.${key}`)}
                checked={draft[key]}
                onCheckedChange={(on) => setDraft({ ...draft, [key]: on })}
              />
            </SyncRow>
          ))}
        </SyncGroup>
        <Button
          variant="outline"
          onClick={() => void act('/api/cloud-sync/on', { choices: draft })}
        >
          {t('cloudsync.turn_on')}
        </Button>
        {error}
      </div>
    );

  const stopped = view.status.stopped;
  const usageOf = (name: string) => view.usage.collections?.find((c) => c.name === name);
  const listed = (name: string) => view.collections.find((c) => c.name === name);
  const setChoice = (key: ChoiceKey, on: boolean, remove: boolean) =>
    void act(
      '/api/cloud-sync/choices',
      { choices: { ...view.choices, [key]: on }, remove_from_cloud: remove ? [key] : [] },
      'PUT',
    );
  const nameOf = (id: string) => devices.data?.devices.find((d) => d.id === id)?.name ?? id;
  const later = view.collections.filter((c) => LATER[c.name]);
  const locked = later.some((c) => !c.entitled);
  const deviceList = devices.data?.devices ?? [];

  return (
    <div data-slot="cloud-sync-panel" className="space-y-4 px-4 py-3">
      {stopped === 'device_revoked' && (
        <div role="alert" className={cn(CALLOUT, 'flex flex-wrap items-center gap-2')}>
          <span className="min-w-0 flex-1">{t('cloudsync.device_revoked')}</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void act('/api/cloud-sync/on', { choices: view.choices, new_device: true })
            }
          >
            {t('cloudsync.set_up_again')}
          </Button>
        </div>
      )}
      {stopped === 'account_suspended' && (
        <p role="alert" className={CALLOUT}>
          {t('cloudsync.account_suspended')}
        </p>
      )}
      {view.status.storage_full && (
        <p role="alert" className={CALLOUT}>
          {t('cloudsync.storage_full', {
            used: size(view.usage.used_bytes),
            limit: size(view.usage.limit_bytes),
          })}
        </p>
      )}
      {view.status.offline && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('account.offline')}
        </p>
      )}
      {view.status.pending_mass_delete && (
        <div role="alert" className={cn(CALLOUT, 'flex flex-wrap items-center gap-2')}>
          <span className="min-w-0 flex-1">
            {t('cloudsync.mass_delete', { count: view.status.pending_mass_delete.count })}
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void act('/api/cloud-sync/confirm-deletes')}
          >
            {t('cloudsync.confirm_remove')}
          </Button>
        </div>
      )}
      {view.status.conflicts.length > 0 && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('cloudsync.conflict', { keys: view.status.conflicts.at(-1)!.keys.join(', ') })}
        </p>
      )}

      <div>
        <SyncGroup id="sync-choose-heading" title={t('cloudsync.choose')}>
          {CHOICES.map((key) => {
            const server = listed(key);
            const paused = server ? !server.enabled : Boolean(view.status.paused[key]);
            const used = usageOf(key);
            return (
              <SyncRow
                key={key}
                id={`sync-${key}`}
                icon={ICONS[key]}
                name={t(`cloudsync.${key}`)}
                meta={
                  paused
                    ? t('cloudsync.paused')
                    : view.status.needs_update.includes(key)
                      ? t('cloudsync.needs_update', { area: t(`cloudsync.${key}`) })
                      : used
                        ? t('cloudsync.size', { size: size(used.bytes) })
                        : undefined
                }
              >
                <Switch
                  aria-label={t(`cloudsync.${key}`)}
                  checked={view.choices[key]}
                  onCheckedChange={(on) => (on ? setChoice(key, true, false) : setTurningOff(key))}
                />
              </SyncRow>
            );
          })}
          {later.map((c) => (
            <SyncRow
              key={c.name}
              id={`sync-${c.name}`}
              icon={ICONS[c.name] ?? FolderIcon}
              name={t(LATER[c.name])}
              muted={!c.entitled}
              badge={
                c.entitled ? undefined : (
                  <Badge
                    variant="outline"
                    data-testid="sync-pro-badge"
                    className="h-4 px-1.5 text-[10px]"
                  >
                    {t('cloudsync.pro')}
                  </Badge>
                )
              }
              meta={c.entitled && !c.enabled ? t('cloudsync.paused') : undefined}
            >
              <Switch
                aria-label={t(LATER[c.name])}
                aria-describedby={c.entitled ? undefined : 'sync-pro-upsell'}
                checked={false}
                disabled
              />
            </SyncRow>
          ))}
        </SyncGroup>
        {turningOff && (
          <div role="dialog" className={cn(CALLOUT, 'mt-2 space-y-2')}>
            <p>{t('cloudsync.turned_off_prompt', { area: t(`cloudsync.${turningOff}`) })}</p>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setChoice(turningOff, false, false);
                  setTurningOff(null);
                }}
              >
                {t('cloudsync.keep_in_cloud')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setChoice(turningOff, false, true);
                  setTurningOff(null);
                }}
              >
                {t('cloudsync.remove_from_cloud', {
                  count: usageOf(turningOff)?.items ?? 0,
                  size: size(usageOf(turningOff)?.bytes),
                })}
              </Button>
            </div>
          </div>
        )}
        {locked && (
          <p
            id="sync-pro-upsell"
            data-testid="sync-pro-upsell"
            className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground"
          >
            <LockIcon aria-hidden="true" className="size-3.5 shrink-0" />
            <span>{t('cloudsync.pro_upsell')}</span>
            {planUrl ? (
              <ExternalLink
                href={planUrl}
                variant="link"
                showIcon={false}
                className="h-auto px-0 text-xs"
              >
                {t('supportPlans.get_pro')}
              </ExternalLink>
            ) : null}
          </p>
        )}
      </div>

      {view.other_devices.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {view.other_devices.map((other) => (
            <Button
              key={other.device_id}
              size="sm"
              variant="outline"
              onClick={() => void act(`/api/cloud-sync/use-choices-of/${other.device_id}`)}
            >
              {t('cloudsync.use_choices_of', { device: nameOf(other.device_id) })}
            </Button>
          ))}
        </div>
      )}

      {deviceList.length > 0 && (
        <SyncGroup id="sync-devices-heading" title={t('cloudsync.devices')}>
          {deviceList.map((device) => {
            const here = device.current || device.id === view.device_id;
            const seen = device.last_seen_at
              ? formatRelative(device.last_seen_at, locale ?? 'en')
              : '';
            const details = [
              device.os_version || device.platform,
              seen ? t('cloudsync.last_active', { time: seen }) : null,
            ].filter(Boolean);
            return (
              <SyncRow
                key={device.id}
                id={`sync-device-${device.id}`}
                icon={device.platform === 'macos' ? LaptopIcon : MonitorIcon}
                name={device.name}
                badge={
                  here ? (
                    <Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
                      {t('cloudsync.this_device')}
                    </Badge>
                  ) : undefined
                }
                meta={details.length > 0 ? details.join(' · ') : undefined}
              >
                {/* This device leaves sync by switching it off; Remove is for the others. */}
                {!here && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="-me-2"
                    onClick={() => void act(`/api/cloud-sync/devices/${device.id}/revoke`)}
                  >
                    {t('cloudsync.remove_device')}
                  </Button>
                )}
              </SyncRow>
            );
          })}
        </SyncGroup>
      )}
      {error}
    </div>
  );
}
