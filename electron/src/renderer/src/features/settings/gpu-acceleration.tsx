import { ZapIcon } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { GpuOfferText, useGpuOffer } from '@/components/gpu-offer';
import { apiJson } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';

type Params = Record<string, string | number | null | undefined>;

export interface GpuReport {
  platform: string;
  gpus: Array<{ vendor: string; name: string; vram_gb: number }>;
  torch: { kind: string; version: string | null };
  /** Host verdict code, e.g. `amd_cuda_build`; rendered via `settings.gpu_report_state_*`. */
  state: string;
  params: Params;
  /** Option codes, rendered via `settings.gpu_report_option_*`. */
  options: string[];
  engines: Array<{
    id: string;
    name: string;
    kind: 'tts' | 'asr';
    available: boolean;
    code: string;
    params: Params;
    reason: string | null;
  }>;
}

// The set of codes the backend can emit (core/gpu_report.py). Anything else
// falls back to the generic key so a newer backend never renders a raw key.
const STATES = new Set([
  'accelerated',
  'accelerated_caveat',
  'pinned_cpu',
  'no_gpu',
  'probe_failed',
  'amd_cuda_build',
  'amd_cpu_build',
  'amd_rocm_no_device',
  'nvidia_cpu_build',
  'nvidia_cuda_unavailable',
  'nvidia_rocm_build',
  'intel_unsupported',
  'kernels_missing',
]);
const OPTIONS = new Set([
  'vulkan_engines',
  'rocm_windows_manual',
  'rocm_variant_linux',
  'rocm_device_access',
  'reinstall_cuda_torch',
  'update_nvidia_driver',
  'install_matching_torch',
]);
const ENGINE_CODES = new Set([
  'gpu',
  'gpu_caveat',
  'cpu_by_design',
  'host_gpu_unusable',
  'host_gpu_unusable_rocm',
  'host_gpu_unusable_no_amd_path',
  'pinned_cpu',
  'no_family_path',
  'no_gpu',
  'needs_accelerator',
  'not_installed',
  'unknown',
]);

/** The GPU offer's permanent home: shown while the host qualifies and the
 * GGUF engine is not in use, whatever was answered on the one-time banner. */
function GpuOfferRow() {
  const { t } = useTranslation();
  const offer = useGpuOffer();
  const status = offer.status;
  if (!status?.eligible || status.active) return null;
  return (
    <SettingsRow
      id="gpu-offer"
      title={t('gpuOffer.title')}
      description={
        <span className="block space-y-1">
          <GpuOfferText status={status} />
          {offer.needsLicence && (
            <span className="block text-xs">{t('gpuOffer.licence_then_retry')}</span>
          )}
          {offer.failed && (
            <span role="alert" className="block text-xs text-destructive">
              {t('common.error')}
            </span>
          )}
        </span>
      }
    >
      <Button size="sm" disabled={offer.busy} onClick={() => void offer.answer(true)}>
        <ZapIcon />
        {t('gpuOffer.accept')}
      </Button>
    </SettingsRow>
  );
}

export function GpuAcceleration() {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['gpu-report'],
    queryFn: ({ signal }) => apiJson<GpuReport>('/api/settings/gpu-report', { signal }),
  });
  const report = query.data;
  if (!report?.state) {
    return query.isError ? (
      <SettingsSection icon={ZapIcon} title={t('settings.gpu_report_title')}>
        <p className="p-4 text-sm text-muted-foreground">{t('settings.gpu_report_unavailable')}</p>
      </SettingsSection>
    ) : null;
  }
  const state = STATES.has(report.state) ? report.state : 'unknown';
  const engines = (report.engines ?? []).filter(
    (engine) => ENGINE_CODES.has(engine.code) && engine.code !== 'not_installed',
  );
  const onGpu = engines.filter((engine) => engine.code.startsWith('gpu')).length;
  return (
    <SettingsSection icon={ZapIcon} title={t('settings.gpu_report_title')}>
      <GpuOfferRow />
      <SettingsRow
        id="gpu-report-summary"
        title={t('settings.gpu_report_summary')}
        description={t('settings.gpu_report_state_' + state, {
          ...report.params,
          torch: report.torch?.version ?? report.torch?.kind ?? '',
        })}
      >
        <span className="text-sm text-muted-foreground">
          {report.gpus.length
            ? report.gpus
                .map((gpu) => (gpu.vram_gb ? `${gpu.name} (${gpu.vram_gb} GB)` : gpu.name))
                .join(', ')
            : t('settings.gpu_report_no_gpu_found')}
        </span>
      </SettingsRow>
      {report.options.some((option) => OPTIONS.has(option)) && (
        <div className="px-4 py-3">
          <h3 className="text-sm font-medium">{t('settings.gpu_report_options')}</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {report.options
              .filter((option) => OPTIONS.has(option))
              .map((option) => (
                <li key={option}>{t('settings.gpu_report_option_' + option)}</li>
              ))}
          </ul>
        </div>
      )}
      {engines.length > 0 && (
        <details className="px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium">
            {engines.some((engine) => engine.code === 'unknown')
              ? t('common.details')
              : t('settings.gpu_report_engines', { gpu: onGpu, total: engines.length })}
          </summary>
          <ul className="mt-2 divide-y divide-border/50 text-sm">
            {engines.map((engine) => (
              <li
                key={`${engine.kind}:${engine.id}`}
                className="flex flex-col gap-0.5 py-2 @xl:flex-row @xl:justify-between @xl:gap-4"
              >
                <span className="min-w-0 break-words">
                  {engine.name}{' '}
                  <span className="text-xs text-muted-foreground">
                    {t('settings.gpu_report_kind_' + engine.kind)}
                  </span>
                </span>
                <span className="text-muted-foreground @xl:text-right">
                  {engine.code === 'unknown' ? t('common.unknown') : t('settings.gpu_report_engine_' + engine.code, engine.params)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </SettingsSection>
  );
}
