import { useTranslation } from 'react-i18next';
import { useDeviceUsage } from '@/hooks/use-device-usage';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

function valid(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

export function LiveDeviceUsage({ open }: { open: boolean }) {
  const { t, i18n } = useTranslation();
  const query = useDeviceUsage(open);
  const backend = useBackendStatus();
  // Don't keep presenting an old successful sample as live after an error.
  const data = !query.isError && isBackendReachable(backend.stage) ? query.data : undefined;
  const number = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  const percent = new Intl.NumberFormat(i18n.language, {
    style: 'percent',
    maximumFractionDigits: 0,
  });
  const gb = new Intl.NumberFormat(i18n.language, {
    style: 'unit',
    unit: 'gigabyte',
    maximumFractionDigits: 1,
  });
  const unavailable = t('modelSettings.unavailable');
  const usage = (value: number | null | undefined) =>
    valid(value) ? percent.format(Math.min(value, 100) / 100) : unavailable;
  const memory = (used: number | undefined, total: number | undefined) =>
    valid(used) && valid(total) && total > 0
      ? `${number.format(used)} / ${gb.format(total)}`
      : unavailable;
  const ratio = (used: number | null | undefined, total = 100) =>
    valid(used) && valid(total) && total > 0 ? Math.min(100, (used / total) * 100) : null;
  const metrics = [
    { label: t('settings.device_family_cpu'), value: usage(data?.cpu), fill: ratio(data?.cpu) },
    {
      label: t('settings.device_family_gpu'),
      value: usage(data?.gpu_utilization),
      fill: ratio(data?.gpu_utilization),
    },
    {
      label: t('about.ram'),
      value: memory(data?.ram, data?.total_ram),
      fill: data ? ratio(data.ram, data.total_ram) : null,
    },
    {
      // /sysinfo falls back to this process's torch allocation when device-wide
      // GPU telemetry is unavailable. Don't label that as whole-device usage.
      label: t(
        data && data.gpu_utilization == null && data.total_vram > 0
          ? 'performanceHardware.appVram'
          : 'about.vram',
      ),
      value: memory(data?.vram, data?.total_vram),
      fill: data ? ratio(data.vram, data.total_vram) : null,
    },
  ];
  return (
    <section
      aria-label={t('performanceHardware.liveUsage')}
      data-slot="live-device-usage"
      className="space-y-2 rounded-lg bg-muted/40 p-3"
    >
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="font-medium">{t('performanceHardware.liveUsage')}</span>
        {query.isError ? (
          <button
            type="button"
            onClick={() => void query.refetch()}
            className="rounded text-muted-foreground underline outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t('common.retry')}
          </button>
        ) : (
          <span className="text-muted-foreground">
            {t(
              !isBackendReachable(backend.stage)
                ? 'modelSettings.unavailable'
                : !data
                  ? 'preferences.loading'
                  : 'performanceHardware.everyTwoSeconds',
            )}
          </span>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        {metrics.map(({ label, value, fill }) => (
          <div key={label} className="min-w-0 space-y-1">
            <dt className="text-[11px] text-muted-foreground">{label}</dt>
            <dd className="text-xs font-medium tabular-nums" title={value}>
              {value}
            </dd>
            <div className="h-0.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
                style={{ width: `${fill ?? 0}%` }}
              />
            </div>
          </div>
        ))}
      </dl>
    </section>
  );
}
