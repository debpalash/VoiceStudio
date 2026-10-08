import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  CheckIcon,
  CopyIcon,
  CpuIcon,
  HardDriveDownloadIcon,
  LogInIcon,
  MemoryStickIcon,
  PencilIcon,
  PlayCircleIcon,
  ShieldCheckIcon,
  Trash2Icon,
} from 'lucide-react';
import { PipelineFailure } from '@/components/pipeline-failure';
import { ComputeVendorIcon } from '@/components/compute-vendor-icon';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { apiJson, describeError } from '@/lib/api/client';
import { isImeComposing } from '@/lib/ime';
import { cn } from '@/lib/utils';
import { SettingsRow, SettingsSection } from './settings-layout';
import { InboundWorkersSettings } from './inbound-workers-settings';
import { ComputeTargetChoices } from '@/components/compute-target-choices';
import { useComputeTarget } from '@/hooks/use-compute-target';

interface WorkerBreaker {
  summary: string;
}

interface Worker {
  id: string;
  name: string;
  enabled: boolean;
  connected?: boolean;
  consent_granted?: boolean;
  endpoint?: string;
  address?: string;
  host?: {
    hostname?: string;
    os?: string;
    arch?: string;
    cpu_count?: number;
    system_memory_bytes?: number;
    gpus?: Array<{
      vendor?: string;
      model?: string;
      backend?: string;
      memory_bytes?: number;
      free_memory_bytes?: number;
      driver_version?: string;
      compute_capability?: string;
    }>;
  };
  last_seen_at?: number | null;
  latency_ms?: number;
  active_tasks?: number;
  available_slots?: number;
  resident_models?: string[];
  breakers?: WorkerBreaker[];
}

interface WorkersState {
  enabled: boolean;
  running: boolean;
  startup_error?: string | null;
  endpoint?: string;
  queue_depth: number;
  workers: Worker[];
}

interface Enrollment {
  token: string;
  expires_at: number;
}

interface AgentState {
  running: boolean;
  enrolled: boolean;
  endpoint?: string;
  last_error?: string;
  env_pinned?: boolean;
}

function relativeSeen(seconds: number | null | undefined, t: TFunction) {
  if (!seconds) return '';
  const minutes = Math.max(0, (Date.now() / 1000 - seconds) / 60);
  if (minutes < 1) return t('settings.workers_seen_now');
  if (minutes < 60) return t('settings.workers_seen_min', { count: Math.round(minutes) });
  return t('settings.workers_seen_hr', { count: Math.round(minutes / 60) });
}

export function formatHardwareMemory(bytes?: number) {
  if (!bytes || bytes < 0) return '';
  const gib = bytes / 1024 ** 3;
  return `${gib >= 10 ? Math.round(gib) : gib.toFixed(1)} GB`;
}

function EnrollmentSecret({
  value,
  expiresAt,
  onDone,
}: {
  value: string;
  expiresAt: number;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [qr, setQr] = useState('');
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let active = true;
    void QRCode.toDataURL(value)
      .then((url) => active && setQr(url))
      .catch(() => {
        if (active) setQr('');
      });
    return () => {
      active = false;
    };
  }, [value]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.ceil(expiresAt - now / 1000));
  const expiry = seconds
    ? t('settings.workers_token_expires_in', {
        time: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`,
      })
    : t('settings.workers_token_expired');
  return (
    <div className="m-4 grid gap-4 rounded-xl border border-primary/25 bg-primary/[0.06] p-4 @xl:grid-cols-[1fr_auto]">
      <div className="min-w-0 space-y-3">
        <div>
          <p className="text-sm font-medium">{t('settings.workers_token_once')}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('settings.workers_token_qr_hint')}
          </p>
        </div>
        <code className="block max-h-28 overflow-auto break-all rounded-lg bg-background/70 p-3 text-xs">
          {value}
        </code>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!seconds}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(value);
                setCopied(true);
                setCopyError(false);
              } catch {
                setCopied(false);
                setCopyError(true);
              }
            }}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
            {t(copied ? 'settings.workers_copied' : 'settings.workers_copy')}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDone}>
            {t('settings.workers_secret_done')}
          </Button>
          <span className={cn('text-xs text-muted-foreground', !seconds && 'text-destructive')}>
            {expiry}
          </span>
          {copyError && (
            <span className="text-xs text-destructive">{t('settings.workers_copy_failed')}</span>
          )}
        </div>
      </div>
      {qr && seconds > 0 && (
        <img
          src={qr}
          alt={t('settings.workers_qr_alt')}
          className="size-36 rounded-lg bg-white p-2"
        />
      )}
    </div>
  );
}

function WorkerRow({
  worker,
  busy,
  confirming,
  act,
  setConfirming,
}: {
  worker: Worker;
  busy: boolean;
  confirming: boolean;
  act: (work: () => Promise<unknown>) => void;
  setConfirming: (value: boolean) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(worker.name);
  const paused = Boolean(worker.breakers?.length);
  const approved = worker.consent_granted !== false;
  const healthy = Boolean(worker.connected && worker.enabled && !paused && approved);
  const active = worker.active_tasks ?? 0;
  const slots = active + (worker.available_slots ?? 0);
  const load = slots ? Math.min(100, Math.round((active / slots) * 100)) : 0;
  const gpu = worker.host?.gpus?.[0];
  const ram = formatHardwareMemory(worker.host?.system_memory_bytes);
  const vram = formatHardwareMemory(gpu?.memory_bytes);
  const status = !worker.enabled
    ? t('settings.workers_status_disabled')
    : paused
      ? t('settings.workers_status_paused')
      : worker.connected
        ? t('settings.workers_status_online')
        : t('settings.workers_status_offline');
  const meta = [
    worker.address || worker.endpoint || worker.host?.hostname,
    !worker.connected && worker.last_seen_at
      ? t('settings.workers_last_seen', { when: relativeSeen(worker.last_seen_at, t) })
      : '',
    worker.resident_models?.slice(0, 3).join(', '),
  ].filter(Boolean);
  const commit = () => {
    setEditing(false);
    const name = draft.trim();
    if (name && name !== worker.name) {
      act(() =>
        apiJson(`/workers/${encodeURIComponent(worker.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({ name }),
        }),
      );
    }
  };
  return (
    <div className="group px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span
          aria-hidden="true"
          className={cn(
            'size-2 shrink-0 rounded-full',
            !worker.enabled
              ? 'bg-muted-foreground/30'
              : paused
                ? 'bg-amber-400'
                : worker.connected
                  ? 'bg-emerald-400'
                  : 'bg-destructive',
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {editing ? (
              <Input
                autoFocus
                value={draft}
                maxLength={120}
                aria-label={t('settings.workers_rename')}
                className="h-7 max-w-64"
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                  if (isImeComposing(event)) return;
                  if (event.key === 'Enter') commit();
                  if (event.key === 'Escape') {
                    setDraft(worker.name);
                    setEditing(false);
                  }
                }}
              />
            ) : (
              <button
                type="button"
                className="flex min-w-0 items-center gap-1.5 rounded font-medium outline-none hover:text-primary focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setEditing(true)}
              >
                <span className="truncate">{worker.name}</span>
                <PencilIcon className="size-3 opacity-45 transition-opacity group-hover:opacity-100" />
              </button>
            )}
            <Badge variant={healthy ? 'outline' : paused ? 'secondary' : 'ghost'}>{status}</Badge>
            {!approved && (
              <Badge variant="destructive">{t('settings.workers_needs_consent')}</Badge>
            )}
            {worker.connected && worker.latency_ms ? (
              <span className="font-mono text-[11px] text-muted-foreground">
                {worker.latency_ms < 1 ? '<1 ms' : `${Math.round(worker.latency_ms)} ms`}
              </span>
            ) : null}
          </div>
          {meta.length > 0 && (
            <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground/70">
              {meta.join(' · ')}
            </p>
          )}
          {(worker.host?.cpu_count || ram || gpu?.model) && (
            <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
              {worker.host?.cpu_count ? (
                <span className="inline-flex items-center gap-1.5 rounded-md bg-muted/45 px-2 py-1">
                  <CpuIcon className="size-3.5" aria-hidden="true" />
                  {t('settings.device_family_cpu')} {worker.host.cpu_count}
                </span>
              ) : null}
              {ram ? (
                <span className="inline-flex items-center gap-1.5 rounded-md bg-muted/45 px-2 py-1">
                  <MemoryStickIcon className="size-3.5" aria-hidden="true" />
                  {t('about.ram')} {ram}
                </span>
              ) : null}
              {gpu?.model ? (
                <span className="inline-flex min-w-0 items-center gap-1.5 rounded-md bg-muted/45 px-2 py-1">
                  <ComputeVendorIcon
                    runtime={gpu.vendor || gpu.backend}
                    className="size-3.5 shrink-0"
                  />
                  <span className="max-w-72 truncate">{gpu.model}</span>
                  {vram ? <span className="whitespace-nowrap">{vram}</span> : null}
                  {gpu.compute_capability ? (
                    <span className="whitespace-nowrap uppercase">
                      {gpu.backend || t('settings.device_family_gpu')} {gpu.compute_capability}
                    </span>
                  ) : null}
                  {gpu.driver_version ? (
                    <span className="whitespace-nowrap font-mono">{gpu.driver_version}</span>
                  ) : null}
                </span>
              ) : null}
            </div>
          )}
          {worker.connected && (
            <div className="mt-2 flex max-w-64 items-center gap-2">
              <span className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                <span
                  className="block h-full rounded-full bg-primary"
                  style={{ width: `${load}%` }}
                />
              </span>
              <span className="text-xs text-muted-foreground">
                {t('settings.workers_load', { active, slots })}
              </span>
            </div>
          )}
          {paused && <p className="mt-1 text-xs text-amber-500">{worker.breakers?.[0]?.summary}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {!approved && (
            <Button
              size="sm"
              disabled={busy}
              onClick={() =>
                act(() =>
                  apiJson(`/workers/${encodeURIComponent(worker.id)}/consent`, { method: 'POST' }),
                )
              }
            >
              <ShieldCheckIcon />
              {t('settings.workers_approve')}
            </Button>
          )}
          {paused && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() =>
                act(() =>
                  apiJson(`/workers/${encodeURIComponent(worker.id)}/resume`, { method: 'POST' }),
                )
              }
            >
              <PlayCircleIcon />
              {t('settings.workers_resume')}
            </Button>
          )}
          {confirming ? (
            <>
              <span className="max-w-64 text-xs text-muted-foreground">
                {t('settings.workers_remove_confirm', { name: worker.name })}
              </span>
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={() =>
                  act(() =>
                    apiJson(`/workers/${encodeURIComponent(worker.id)}`, { method: 'DELETE' }),
                  )
                }
              >
                {t('settings.workers_remove')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                {t('common.cancel')}
              </Button>
            </>
          ) : (
            <div className="flex items-center gap-1 opacity-50 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <Button
                size="icon-sm"
                variant="ghost"
                disabled={busy}
                aria-label={t(
                  worker.enabled ? 'settings.workers_disable' : 'settings.workers_enable_one',
                )}
                onClick={() =>
                  act(() =>
                    apiJson(`/workers/${encodeURIComponent(worker.id)}`, {
                      method: 'PATCH',
                      body: JSON.stringify({ enabled: !worker.enabled }),
                    }),
                  )
                }
              >
                <CheckIcon />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                disabled={busy}
                aria-label={t('settings.workers_remove')}
                onClick={() => setConfirming(true)}
              >
                <Trash2Icon />
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function WorkersSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [joinCode, setJoinCode] = useState('');
  const [confirming, setConfirming] = useState<string | null>(null);
  const workers = useQuery({
    queryKey: ['workers'],
    queryFn: ({ signal }) => apiJson<WorkersState>('/workers', { signal }),
    refetchInterval: (query) => (query.state.data?.running ? 5000 : false),
  });
  const agent = useQuery({
    queryKey: ['workers', 'agent'],
    queryFn: ({ signal }) => apiJson<AgentState>('/workers/agent', { signal }),
    enabled: Boolean(workers.data?.enabled),
    refetchInterval: 5000,
  });
  const target = useComputeTarget(Boolean(workers.data?.enabled));
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['workers'] });
    setConfirming(null);
  };
  const act = (name: string, work: () => Promise<unknown>, after?: () => void) => {
    if (busy) return;
    setBusy(name);
    setError('');
    setNotice('');
    void work()
      .then(async () => {
        after?.();
        await refresh();
      })
      .catch((error) => setError(describeError(error)))
      .finally(() => setBusy(''));
  };
  const state = workers.data;
  const online = state?.workers.filter((worker) => worker.connected && worker.enabled).length ?? 0;

  return (
    <>
      <SettingsSection icon={CpuIcon} title={t('settings.workers_title')}>
        <SettingsRow
          id="workers-enabled"
          title={t('settings.workers_enable')}
          description={t('settings.workers_enable_hint')}
        >
          {state?.running && (
            <Badge variant="outline" className={online ? 'text-emerald-500' : undefined}>
              {t(online ? 'settings.workers_summary_online' : 'settings.workers_summary_none', {
                count: online,
              })}
            </Badge>
          )}
          <Switch
            checked={Boolean(state?.enabled)}
            disabled={!state || Boolean(busy)}
            aria-label={t('settings.workers_enable')}
            onCheckedChange={(enabled) =>
              act(
                'enabled',
                () =>
                  apiJson('/workers/enabled', {
                    method: 'POST',
                    body: JSON.stringify({ enabled }),
                  }),
                () => !enabled && setEnrollment(null),
              )
            }
          />
        </SettingsRow>
        {state?.enabled && !state.running && state.startup_error && (
          <div className="p-4">
            <PipelineFailure
              fallback={`${t('settings.workers_port_conflict')} ${state.startup_error}`}
            />
          </div>
        )}
        {state?.enabled && state.running && (
          <>
            {target.data && target.data.targets.length > 1 && (
              <SettingsRow
                id="workers-target"
                title={t('compute.title')}
                description={target.data.active.reason}
              >
                <div className="w-full min-w-60 max-w-sm">
                  <ComputeTargetChoices data={target.data} disabled={Boolean(busy)} />
                </div>
              </SettingsRow>
            )}
            <SettingsRow
              id="workers-endpoint"
              title={t('settings.workers_endpoint')}
              description={t('settings.workers_endpoint_hint')}
            >
              <code className="max-w-full break-all rounded-md bg-muted/50 px-2 py-1 text-xs">
                {state.endpoint || '—'}
              </code>
            </SettingsRow>
            <SettingsRow
              id="workers-add"
              title={t('settings.workers_add')}
              description={t('settings.workers_add_hint_qr')}
            >
              <Button
                size="sm"
                disabled={Boolean(busy)}
                onClick={() =>
                  act('token', async () => {
                    const value = await apiJson<Enrollment>('/workers/enrollments', {
                      method: 'POST',
                      body: JSON.stringify({ ttl_seconds: 900 }),
                    });
                    setEnrollment(value);
                  })
                }
              >
                {t('settings.workers_new_token')}
              </Button>
            </SettingsRow>
            {enrollment && (
              <EnrollmentSecret
                value={enrollment.token}
                expiresAt={enrollment.expires_at}
                onDone={() => setEnrollment(null)}
              />
            )}
            {state.workers.length ? (
              state.workers.map((worker) => (
                <WorkerRow
                  key={worker.id}
                  worker={worker}
                  busy={Boolean(busy)}
                  confirming={confirming === worker.id}
                  setConfirming={(value) => setConfirming(value ? worker.id : null)}
                  act={(work) => act(`worker-${worker.id}`, work)}
                />
              ))
            ) : (
              <div className="px-4 py-4">
                <p className="text-sm text-muted-foreground">{t('settings.workers_none')}</p>
                <ol className="mt-3 space-y-2">
                  {[1, 2, 3].map((step) => (
                    <li key={step} className="flex items-start gap-2 text-xs text-muted-foreground">
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[10px]">
                        {step}
                      </span>
                      {t(`settings.workers_step_${step}`)}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </>
        )}
        {(error || workers.isError) && (
          <div className="p-4">
            <PipelineFailure
              fallback={error || describeError(workers.error)}
              onDismiss={error ? () => setError('') : undefined}
              action={
                workers.isError ? (
                  <Button size="sm" variant="ghost" onClick={() => void workers.refetch()}>
                    {t('common.retry')}
                  </Button>
                ) : undefined
              }
            />
          </div>
        )}
      </SettingsSection>

      {state?.enabled && (
        <SettingsSection icon={HardDriveDownloadIcon} title={t('settings.worker_join_title')}>
          <SettingsRow
            id="worker-agent"
            title={t('settings.worker_join_take_work')}
            description={
              agent.data?.endpoint ||
              (agent.data?.enrolled
                ? t('settings.worker_join_no_endpoint')
                : t('settings.worker_join_desc'))
            }
          >
            {agent.data?.enrolled && (
              <>
                <Badge variant={agent.data.running ? 'outline' : 'secondary'}>
                  {t(
                    agent.data.running
                      ? 'settings.worker_join_working'
                      : 'settings.worker_join_stopped',
                  )}
                </Badge>
                <Switch
                  checked={agent.data.running}
                  disabled={Boolean(busy) || agent.data.env_pinned}
                  aria-label={t('settings.worker_join_take_work')}
                  onCheckedChange={(enabled) =>
                    act('agent', () =>
                      apiJson('/workers/agent/enabled', {
                        method: 'POST',
                        body: JSON.stringify({ enabled }),
                      }),
                    )
                  }
                />
              </>
            )}
          </SettingsRow>
          {agent.data?.env_pinned && (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              {t('settings.worker_join_env')}
            </p>
          )}
          <SettingsRow
            id="worker-join-code"
            title={t(
              agent.data?.enrolled ? 'settings.worker_join_rejoin' : 'settings.worker_join_code',
            )}
            description={t('settings.worker_join_code_hint')}
          >
            <Input
              value={joinCode}
              type="password"
              name="worker-join-code"
              autoComplete="off"
              spellCheck={false}
              translate="no"
              placeholder={t('settings.worker_join_placeholder')}
              aria-label={t('settings.worker_join_code')}
              className="min-w-64 font-mono"
              disabled={Boolean(busy) || agent.data?.env_pinned}
              onChange={(event) => setJoinCode(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && joinCode.trim() && !busy) {
                  act(
                    'join',
                    () =>
                      apiJson('/workers/agent/join', {
                        method: 'POST',
                        body: JSON.stringify({ token: joinCode.trim() }),
                      }),
                    () => {
                      setJoinCode('');
                      setNotice(t('settings.worker_join_ok'));
                    },
                  );
                }
              }}
            />
            <Button
              size="sm"
              disabled={!joinCode.trim() || Boolean(busy) || agent.data?.env_pinned}
              onClick={() =>
                act(
                  'join',
                  () =>
                    apiJson('/workers/agent/join', {
                      method: 'POST',
                      body: JSON.stringify({ token: joinCode.trim() }),
                    }),
                  () => {
                    setJoinCode('');
                    setNotice(t('settings.worker_join_ok'));
                  },
                )
              }
            >
              <LogInIcon />
              {t('settings.worker_join')}
            </Button>
          </SettingsRow>
          {agent.data?.last_error && (
            <div className="p-4">
              <PipelineFailure fallback={agent.data.last_error} />
            </div>
          )}
          {notice && (
            <p role="status" className="px-4 py-3 text-sm text-emerald-500">
              {notice}
            </p>
          )}
        </SettingsSection>
      )}
      {state?.enabled && <InboundWorkersSettings />}
    </>
  );
}
