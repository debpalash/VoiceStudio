import {
  CpuIcon,
  MemoryStickIcon,
  MonitorUpIcon,
  ServerIcon,
  Trash2Icon,
  ZapIcon,
} from 'lucide-react';
import { GpuAcceleration } from './gpu-acceleration';
import { SystemPreflight } from './system-preflight';
import { PerformanceProfile } from '@/components/performance-profile';
import { familyIcons, modelFamilies } from './model-family';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { apiJson, describeError } from '@/lib/api/client';
import { useAppActivities } from '@/lib/app-activity';
import { relaxWhenBackendBusy } from '@/lib/status-polling';
import { SettingsSection, SettingsRow } from './settings-layout';
import { useSettingsAction } from './use-settings-action';
// @ts-expect-error shared JSX component has no declaration file
import SearchableSelect from '@shared/components/SearchableSelect';
interface DeviceState {
  value: string;
  applied: string;
  effective_family: string;
  available_families: string[];
  env_pinned: boolean;
  override_ignored: boolean;
  restart_required: boolean;
}
interface CudaDeviceState {
  value: string;
  applied: string;
  env_pinned: boolean;
  restart_required: boolean;
  devices: Array<{ index: number; value: string; name: string }>;
}
interface SystemInfo {
  platform: string;
  arch: string;
  python: string;
  generate_timeout_s: number;
  cpu_generate_timeout_s: number;
  generate_timeout_shadowed: boolean;
  cpu_generate_timeout_shadowed: boolean;
}

interface HardwareState {
  cpu: number;
  cpu_model: string;
  cpu_physical_cores: number;
  cpu_logical_cores: number;
  cpu_frequency_ghz: number;
  ram: number;
  total_ram: number;
  gpu_name: string;
  gpu_utilization: number | null;
  vram: number;
  total_vram: number;
  gpu_active: boolean;
}

function UsageBar({ label, value, percent }: { label: string; value: string; percent: number }) {
  const bounded = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
  return (
    <div className="w-full space-y-1.5">
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>{label}</span>
        <span className="shrink-0 tabular-nums text-foreground/80">{value}</span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(bounded)}
        className="h-1.5 overflow-hidden rounded-full bg-muted/80"
      >
        <span
          className="block h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${bounded}%` }}
        />
      </div>
    </div>
  );
}
export function PerformanceSettings() {
  const { t } = useTranslation();
  const info = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<SystemInfo>('/system/info', { signal }),
  });
  const hardware = useQuery({
    queryKey: ['sysinfo'],
    queryFn: ({ signal }) => apiJson<HardwareState>('/sysinfo', { signal }),
    refetchInterval: () => relaxWhenBackendBusy(5000),
  });
  const gb = (value: number | undefined) =>
    value == null || !Number.isFinite(value) ? '-' : value.toFixed(2) + ' GB';
  const memoryPercent = hardware.data?.total_ram
    ? (hardware.data.ram / hardware.data.total_ram) * 100
    : 0;
  return (
    <>
      <SystemPreflight />
      <SettingsSection icon={ZapIcon} title={t('performanceProfile.title')}>
        <div className="p-4">
          <PerformanceProfile variant="settings" />
          <p className="mt-2 text-xs text-muted-foreground">{t('performanceProfile.hint')}</p>
        </div>
        {modelFamilies.map((family) => {
          const Icon = familyIcons[family];
          return (
            <SettingsRow
              key={family}
              id={'performance-' + family}
              title={
                <span className="inline-flex items-center gap-2">
                  <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                  {t('engineSidebar.' + family)}
                </span>
              }
            >
              <PerformanceProfile family={family} variant="settings" />
            </SettingsRow>
          );
        })}
      </SettingsSection>
      <ComputeDevice />
      <GpuAcceleration />
      <CompileSetting />
      <MemoryManagement />
      <SettingsSection icon={CpuIcon} title={t('settings.generate_budget_title')}>
        <p className="p-4 text-sm text-muted-foreground">{t('settings.generate_budget_desc')}</p>
        <BudgetRow
          kind="gpu"
          envKey="OMNIVOICE_GENERATE_TIMEOUT_S"
          current={info.data?.generate_timeout_s}
          shadowed={!!info.data?.generate_timeout_shadowed}
        />
        <BudgetRow
          kind="cpu"
          envKey="OMNIVOICE_CPU_GENERATE_TIMEOUT_S"
          current={info.data?.cpu_generate_timeout_s}
          shadowed={!!info.data?.cpu_generate_timeout_shadowed}
        />
      </SettingsSection>
      <SettingsSection icon={CpuIcon} title={t('engineSidebar.localDevice')} contentVariant="cards">
        <SettingsRow
          className="min-h-32"
          id="hardware-cpu"
          variant="card"
          title={
            <span className="inline-flex items-center gap-2">
              <CpuIcon className="size-4 text-muted-foreground" />
              {t('settings.device_family_cpu')}
            </span>
          }
          description={hardware.data?.cpu_model || t('common.loading')}
        >
          <div className="w-full space-y-2">
            <p className="text-xs tabular-nums text-muted-foreground">
              {hardware.data
                ? [
                    hardware.data.cpu_physical_cores ? `${hardware.data.cpu_physical_cores}C` : '',
                    hardware.data.cpu_logical_cores ? `${hardware.data.cpu_logical_cores}T` : '',
                    hardware.data.cpu_frequency_ghz
                      ? `${hardware.data.cpu_frequency_ghz.toFixed(1)} GHz`
                      : '',
                  ]
                    .filter(Boolean)
                    .join(' · ')
                : t('common.loading')}
            </p>
            <UsageBar
              label={t('settings.device_family_cpu')}
              value={hardware.data ? `${Math.round(hardware.data.cpu)}%` : '—'}
              percent={hardware.data?.cpu ?? 0}
            />
          </div>
        </SettingsRow>
        <SettingsRow
          className="min-h-32"
          id="hardware-gpu"
          variant="card"
          title={
            <span className="inline-flex items-center gap-2">
              <MonitorUpIcon className="size-4 text-muted-foreground" />
              {t('settings.device_family_gpu')}
            </span>
          }
          description={hardware.data?.gpu_name || t('modelSettings.unavailable')}
        >
          <div className="w-full space-y-2">
            <p className="text-xs tabular-nums text-muted-foreground">
              {hardware.data
                ? hardware.data.total_vram > 0
                  ? `${gb(hardware.data.vram)} / ${gb(hardware.data.total_vram)}`
                  : t(hardware.data.gpu_active ? 'engineRuntime.working' : 'engineRuntime.idle')
                : t('common.loading')}
            </p>
            <UsageBar
              label={t('settings.device_family_gpu')}
              value={
                hardware.data?.gpu_utilization != null
                  ? `${Math.round(hardware.data.gpu_utilization)}%`
                  : hardware.data
                    ? t(hardware.data.gpu_active ? 'engineRuntime.working' : 'engineRuntime.idle')
                    : '—'
              }
              percent={hardware.data?.gpu_utilization ?? 0}
            />
          </div>
        </SettingsRow>
        <SettingsRow
          className="min-h-32"
          id="hardware-memory"
          variant="card"
          title={
            <span className="inline-flex items-center gap-2">
              <MemoryStickIcon className="size-4 text-muted-foreground" />
              {t('about.ram')}
            </span>
          }
          description={
            hardware.data
              ? `${gb(hardware.data.ram)} / ${gb(hardware.data.total_ram)}`
              : t('common.loading')
          }
        >
          <UsageBar
            label={t('about.ram')}
            value={hardware.data ? `${Math.round(memoryPercent)}%` : '—'}
            percent={memoryPercent}
          />
        </SettingsRow>
        <SettingsRow
          className="col-span-full min-h-24"
          id="hardware-runtime"
          variant="card"
          title={
            <span className="inline-flex items-center gap-2">
              <ServerIcon className="size-4 text-muted-foreground" />
              {t('about.platform')}
            </span>
          }
          description={
            info.data ? `${info.data.platform} · ${info.data.arch}` : t('common.loading')
          }
        >
          <p className="text-xs text-muted-foreground">
            {t('about.python')}:{' '}
            <span className="font-mono text-foreground/80">{info.data?.python || '—'}</span>
          </p>
        </SettingsRow>
        {(info.isError || hardware.isError) && (
          <div className="col-span-full">
            <ErrorRow
              retry={() => {
                void info.refetch();
                void hardware.refetch();
              }}
            />
          </div>
        )}
      </SettingsSection>
    </>
  );
}

interface LoadedModel {
  id: string;
  name: string;
  checkpoint: string;
  device?: string;
  vram_mb?: number;
  unloadable: boolean;
}

function MemoryManagement() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const activities = useAppActivities();
  const active = Object.values(activities).some((count) => count > 0);
  const [busy, setBusy] = useState<string | null>(null);
  const loaded = useQuery({
    queryKey: ['loaded-models'],
    queryFn: ({ signal }) =>
      apiJson<{ models: LoadedModel[]; count: number }>('/model/loaded', { signal }),
  });
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['loaded-models'] }),
      client.invalidateQueries({ queryKey: ['sidebar-model-status'] }),
      client.invalidateQueries({ queryKey: ['engines'] }),
      client.invalidateQueries({ queryKey: ['sysinfo'] }),
    ]);
  };
  const unload = async (model: LoadedModel) => {
    setBusy(model.id);
    try {
      await apiJson('/model/unload/' + encodeURIComponent(model.id), { method: 'POST' });
      await refresh();
      toast.success(t('modelMaintenance.unloaded'));
    } catch (error) {
      toast.error(t('modelMaintenance.unloadFailed', { message: describeError(error) }));
    } finally {
      setBusy(null);
    }
  };
  const flush = async (unloadAll: boolean) => {
    setBusy(unloadAll ? 'all' : 'cache');
    try {
      const result = await apiJson<{
        ram_after: number;
        vram_after: number;
        unloaded_model: boolean;
      }>(`/system/flush-memory?unload_model=${String(unloadAll)}`, { method: 'POST' });
      await refresh();
      toast.success(
        t('app.toast_flushed', {
          ram: result.ram_after,
          vram: result.vram_after,
          unloaded: result.unloaded_model ? t('app.toast_model_unloaded') : '',
        }),
      );
    } catch (error) {
      toast.error(t('app.toast_flush_failed', { message: describeError(error) }));
    } finally {
      setBusy(null);
    }
  };
  return (
    <SettingsSection icon={MemoryStickIcon} title={t('header.memory_management')}>
      {loaded.isPending ? (
        <p className="p-4 text-sm text-muted-foreground">{t('common.loading')}</p>
      ) : loaded.isError ? (
        <ErrorRow retry={() => void loaded.refetch()} />
      ) : loaded.data.models.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">{t('header.no_models')}</p>
      ) : (
        loaded.data.models.map((model) => (
          <SettingsRow
            key={model.id}
            id={'loaded-' + model.id}
            title={model.name}
            description={[
              model.checkpoint,
              model.device,
              model.vram_mb ? `${model.vram_mb.toFixed(0)} MB` : '',
            ]
              .filter(Boolean)
              .join(' · ')}
          >
            {model.unloadable ? (
              <Button
                size="sm"
                variant="outline"
                disabled={active || busy !== null}
                onClick={() => void unload(model)}
              >
                <MemoryStickIcon />
                {t('header.unload')}
              </Button>
            ) : null}
          </SettingsRow>
        ))
      )}
      <SettingsRow id="flush-caches" title={t('header.flush_caches')}>
        <Button
          size="sm"
          variant="outline"
          disabled={active || busy !== null}
          onClick={() => void flush(false)}
        >
          <ZapIcon />
          {t('header.flush_caches')}
        </Button>
      </SettingsRow>
      <SettingsRow id="unload-all" title={t('header.unload_all_flush')}>
        <Button
          size="sm"
          variant="destructive"
          disabled={
            active || busy !== null || !loaded.data?.models.some((model) => model.unloadable)
          }
          onClick={() => void flush(true)}
        >
          <Trash2Icon />
          {t('header.unload_all_flush')}
        </Button>
      </SettingsRow>
    </SettingsSection>
  );
}
function ErrorRow({ retry }: { retry: () => void }) {
  const { t } = useTranslation();
  return (
    <p role="alert" className="p-4 text-sm text-destructive">
      {t('common.error')}{' '}
      <Button size="sm" variant="ghost" onClick={retry}>
        {t('common.retry')}
      </Button>
    </p>
  );
}
function ComputeDevice() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const cudaAction = useSettingsAction();
  const query = useQuery({
    queryKey: ['compute-device'],
    queryFn: ({ signal }) => apiJson<DeviceState>('/api/settings/compute-device', { signal }),
  });
  const state = query.data;
  const cudaQuery = useQuery({
    queryKey: ['cuda-device'],
    queryFn: ({ signal }) => apiJson<CudaDeviceState>('/api/settings/cuda-device', { signal }),
  });
  const cuda = cudaQuery.data;
  const choose = (value: string) =>
    action.run(async () => {
      const saved = await apiJson<DeviceState>('/api/settings/compute-device', {
        method: 'PUT',
        body: JSON.stringify({ value }),
      });
      client.setQueryData(['compute-device'], saved);
    });
  return (
    <SettingsSection icon={CpuIcon} title={t('settings.compute_device_title')}>
      <SettingsRow
        id="compute-device"
        title={t('settings.compute_device')}
        description={t('settings.compute_device_desc')}
      >
        {['auto', ...new Set(state?.available_families || [])].map((value) => (
          <Button
            key={value}
            size="sm"
            variant={state?.value === value ? 'secondary' : 'ghost'}
            aria-pressed={state?.value === value}
            disabled={!state || action.busy || state.env_pinned}
            onClick={() => void choose(value)}
          >
            {t(
              value === 'auto' ? 'settings.compute_device_auto' : 'settings.device_family_' + value,
            )}
          </Button>
        ))}
      </SettingsRow>
      <SettingsRow id="compute-active" title={t('batch.active')}>
        <span className="text-sm">
          {state ? t('settings.device_family_' + state.effective_family) : t('common.loading')}
        </span>
      </SettingsRow>
      {((cuda?.devices.length || 0) > 1 || cuda?.value !== 'auto') && (
        <SettingsRow
          id="cuda-device"
          title={t('settings.device_family_cuda')}
          description={t('settings.compute_device_restart')}
        >
          <div className="w-72 max-w-full">
            <SearchableSelect
              value={cuda?.value || 'auto'}
              options={[
                { value: 'auto', label: t('settings.compute_device_auto') },
                ...(cuda?.value === 'disabled'
                  ? [{ value: 'disabled', label: t('supportPlans.disabled') }]
                  : []),
                ...(cuda?.devices || []).map((device) => ({
                  value: device.value,
                  label: `${t('settings.device_family_gpu')} ${device.index} — ${device.name}`,
                })),
              ]}
              onChange={(value: string) =>
                void cudaAction.run(async () => {
                  const saved = await apiJson<CudaDeviceState>('/api/settings/cuda-device', {
                    method: 'PUT',
                    body: JSON.stringify({ value }),
                  });
                  client.setQueryData(['cuda-device'], saved);
                })
              }
              disabled={!cuda || cuda.env_pinned || cudaAction.busy}
              ariaLabel={t('settings.device_family_cuda')}
              menuPortal
            />
          </div>
        </SettingsRow>
      )}
      {state?.env_pinned && (
        <p className="p-4 text-sm text-muted-foreground">
          {t('settings.compute_device_env_pinned')}
        </p>
      )}
      {state?.override_ignored && (
        <p className="p-4 text-sm text-muted-foreground">{t('settings.compute_device_ignored')}</p>
      )}
      {state?.restart_required && (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t('settings.compute_device_restart')}
        </p>
      )}
      {cuda?.env_pinned && (
        <p className="p-4 text-sm text-muted-foreground">
          {t('settings.generate_timeout_shadowed_note')}
        </p>
      )}
      {cuda?.restart_required && (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t('settings.compute_device_restart')}
        </p>
      )}
      {(action.error || query.isError || cudaAction.error || cudaQuery.isError) && (
        <ErrorRow
          retry={() => {
            void Promise.all([
              query.refetch({ throwOnError: true }),
              cudaQuery.refetch({ throwOnError: true }),
            ]).then(
              () => {
                action.clearError();
                cudaAction.clearError();
              },
              () => {
                // Preserve the action error until both settings reload successfully.
              },
            );
          }}
        />
      )}
    </SettingsSection>
  );
}
function CompileSetting() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const query = useQuery({
    queryKey: ['torch-compile-disabled'],
    queryFn: ({ signal }) =>
      apiJson<{ enabled: boolean; platform: string }>('/api/settings/perf/torch-compile-disabled', {
        signal,
      }),
  });
  // #2135: live on every platform. This used to be gated to win32 (matching
  // the Tauri UI), which left the Linux/CUDA reporter unable to switch off the
  // torch.compile that was killing their backend.
  return (
    <SettingsSection icon={CpuIcon} title={t('settings.perf_title')}>
      <SettingsRow
        id="torch-compile"
        title={t('settings.perf_torch_compile')}
        description={query.data ? t('settings.perf_torch_compile_note') : undefined}
      >
        <Switch
          aria-label={t('settings.perf_torch_compile')}
          checked={!!query.data?.enabled}
          disabled={action.busy || query.isPending}
          onCheckedChange={(enabled) =>
            void action.run(async () => {
              const state = await apiJson('/api/settings/perf/torch-compile-disabled', {
                method: 'PUT',
                body: JSON.stringify({ enabled }),
              });
              client.setQueryData(['torch-compile-disabled'], state);
            })
          }
        />
      </SettingsRow>
      <p className="p-4 text-xs text-muted-foreground">{t('settings.compute_device_restart')}</p>
      {(action.error || query.isError) && <ErrorRow retry={() => void query.refetch()} />}
    </SettingsSection>
  );
}
function BudgetRow({
  kind,
  envKey,
  current,
  shadowed,
}: {
  kind: 'gpu' | 'cpu';
  envKey: string;
  current?: number;
  shadowed: boolean;
}) {
  const { t } = useTranslation();
  const action = useSettingsAction();
  const dirty = useRef(false);
  const [value, setValue] = useState('');
  const [saveShadowed, setSaveShadowed] = useState(false);
  useEffect(() => {
    if (!dirty.current && current != null) setValue(String(current));
  }, [current]);
  const overridden = shadowed || saveShadowed;
  const number = Number(value);
  const valid = Number.isFinite(number) && number > 0 && number <= 21600;
  const save = () =>
    action.run(async () => {
      const result = await apiJson<{ shadowed?: boolean }>('/system/set-env', {
        method: 'POST',
        body: JSON.stringify({ key: envKey, value: String(number) }),
      });
      setSaveShadowed(!!result.shadowed);
    });
  return (
    <div>
      <SettingsRow
        id={'budget-' + kind}
        title={t('settings.generate_timeout_' + kind)}
        description={t('settings.generate_timeout_' + kind + '_note')}
      >
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) void save();
          }}
        >
          <Input
            className="w-28 tabular-nums"
            type="number"
            min="0.001"
            max="21600"
            step="any"
            aria-label={t('settings.generate_timeout_' + kind)}
            value={value}
            disabled={action.busy || current == null}
            onChange={(event) => {
              dirty.current = true;
              setValue(event.target.value);
              action.reset();
            }}
          />
          <Button type="submit" size="sm" disabled={action.busy || !valid || current == null}>
            {t('common.save')}
          </Button>
        </form>
      </SettingsRow>
      <p
        role={action.saved ? 'status' : undefined}
        className="px-4 pb-3 text-xs text-muted-foreground"
      >
        {t(
          overridden
            ? 'settings.generate_timeout_shadowed_note'
            : 'settings.compute_device_restart',
        )}
      </p>
      {action.error && (
        <p role="alert" className="px-4 pb-3 text-sm text-destructive">
          {t('common.error')}
        </p>
      )}
    </div>
  );
}
