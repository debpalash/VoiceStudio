import { ZapIcon } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
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
]);
const OPTIONS = new Set([
  'vulkan_engines',
  'rocm_windows_manual',
  'rocm_variant_linux',
  'rocm_device_access',
  'reinstall_cuda_torch',
  'update_nvidia_driver',
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
]);

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
            {t('settings.gpu_report_engines', { gpu: onGpu, total: engines.length })}
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
                  {t('settings.gpu_report_engine_' + engine.code, engine.params)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </SettingsSection>
  );
}
