import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ActivityIcon,
  AlertTriangleIcon,
  AudioLinesIcon,
  CheckCircle2Icon,
  CopyIcon,
  CpuIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FolderOpenIcon,
  InfoIcon,
  LanguagesIcon,
  LoaderCircleIcon,
  Mic2Icon,
  MonitorUpIcon,
  XCircleIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { getBridge } from '@/components/bridge';
import { apiJson, describeError } from '@/lib/api/client';
import type { SystemInfo } from '@/lib/api/types';
import { brandIcon } from '@/lib/brand';
import { SettingsActionError } from './settings-action-error';
import { ModelCredits } from './model-credits';
import { SettingsRow, SettingsSection } from './settings-layout';

type CheckStatus = 'ok' | 'warn' | 'fail';

interface DiagnosticCheck {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
  hint?: string | null;
}

interface DiagnosticReport {
  app_version: string;
  platform: string;
  checks: DiagnosticCheck[];
  summary: {
    ok: boolean;
    passed: number;
    warnings: number;
    failures: number;
  };
}

interface DiagnosticBundle {
  path: string;
  filename: string;
}

const FIX_ROUTES: Record<string, string> = {
  ffmpeg: '/settings/media',
  hf_token: '/settings/credentials',
  disk: '/settings/storage',
  data_dir: '/settings/storage',
  engines: '/settings/models',
  gpu_routing: '/settings/performance',
  device: '/settings/performance',
  ram: '/settings/performance',
  deep_synth: '/settings/logs',
};

function reportText(report: DiagnosticReport) {
  const marker: Record<CheckStatus, string> = { ok: '[ OK ]', warn: '[WARN]', fail: '[FAIL]' };
  const lines = [
    `VoiceStudio diagnostics - v${report.app_version} on ${report.platform}`,
    '',
    ...report.checks.flatMap((check) => [
      `${marker[check.status]} ${check.label}: ${check.detail}`,
      ...(check.hint ? [`       hint: ${check.hint}`] : []),
    ]),
    '',
    `${report.summary.passed} ok, ${report.summary.warnings} warning(s), ${report.summary.failures} failure(s)`,
  ];
  return lines.join('\n');
}

export function DiagnosticsSettings() {
  const { t } = useTranslation();
  const bridge = getBridge();
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [action, setAction] = useState<'check' | 'bundle' | 'copy' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const info = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<SystemInfo>('/system/info', { signal }),
  });

  const system = info.data;
  const platform = system
    ? [system.os_version || system.platform, system.arch].filter(Boolean).join(' · ')
    : bridge?.app.platform || navigator.platform;
  const cpu = system
    ? [system.cpu_model, system.cpu_count ? `${system.cpu_count}T` : ''].filter(Boolean).join(' · ')
    : '';
  const gpu = system
    ? [system.gpu_name, system.vram_total_gb ? `${system.vram_total_gb.toFixed(1)} GB` : '']
        .filter(Boolean)
        .join(' · ')
    : '';

  const runCheck = async (): Promise<DiagnosticReport | null> => {
    setAction('check');
    setError(null);
    try {
      const next = await apiJson<DiagnosticReport>('/system/diagnose?network=true');
      setReport(next);
      return next;
    } catch (cause) {
      setError(describeError(cause));
      return null;
    } finally {
      setAction(null);
    }
  };

  const copy = async () => {
    setAction('copy');
    setError(null);
    try {
      const current = report ?? (await apiJson<DiagnosticReport>('/system/diagnose?network=true'));
      setReport(current);
      await navigator.clipboard.writeText(reportText(current));
      toast.success(t('about.copy_diagnostics'));
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setAction(null);
    }
  };

  const buildBundle = async () => {
    setAction('bundle');
    setError(null);
    try {
      const bundle = await apiJson<DiagnosticBundle>('/system/diagnostic-bundle?network=false', {
        method: 'POST',
      });
      toast.success(t('about.bundle_saved', { filename: bundle.filename }));
      await getBridge()?.files.revealPath(bundle.path);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setAction(null);
    }
  };

  return (
    <>
      <SettingsSection icon={InfoIcon} title={t('about.title')}>
        <SettingsRow id="diagnostics-app" title={t('about.app')}>
          <span className="inline-flex items-center gap-2 text-sm font-medium" translate="no">
            <img src={brandIcon} alt="" className="size-5" />
            VoiceStudio
          </span>
        </SettingsRow>
        <SettingsRow id="diagnostics-version" title={t('about.version')}>
          <span className="font-mono text-xs">{bridge?.app.version ?? __APP_VERSION__}</span>
        </SettingsRow>
        <SettingsRow id="diagnostics-platform" title={t('about.platform')}>
          <span className="max-w-xl text-right font-mono text-xs">{platform}</span>
        </SettingsRow>
        <SettingsRow
          id="diagnostics-device"
          title={
            <span className="inline-flex items-center gap-2">
              <CpuIcon className="size-4 text-muted-foreground" aria-hidden="true" />
              {t('about.compute_device')}
            </span>
          }
          description={cpu || undefined}
        >
          <span className="max-w-xl text-right font-mono text-xs">
            {system?.device || t('common.loading')}
          </span>
        </SettingsRow>
        {gpu && (
          <SettingsRow
            id="diagnostics-gpu"
            title={
              <span className="inline-flex items-center gap-2">
                <MonitorUpIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                {t('about.gpu_active')}
              </span>
            }
            description={gpu}
          >
            <span className="text-xs font-medium">
              {t(system?.device && system.device !== 'cpu' ? 'about.yes' : 'about.no')}
            </span>
          </SettingsRow>
        )}
        <SettingsRow id="diagnostics-memory" title={t('about.ram')}>
          <span className="font-mono text-xs">
            {system?.ram_total_gb ? `${system.ram_total_gb.toFixed(1)} GB` : t('common.loading')}
          </span>
        </SettingsRow>
        <SettingsRow id="diagnostics-python" title={t('about.python')}>
          <span className="font-mono text-xs">{system?.python || t('common.loading')}</span>
        </SettingsRow>
        <SettingsRow
          id="diagnostics-tts"
          title={
            <span className="inline-flex items-center gap-2">
              <AudioLinesIcon className="size-4 text-muted-foreground" aria-hidden="true" />
              {t('engineSidebar.tts')}
            </span>
          }
        >
          <span className="max-w-xl break-all text-right font-mono text-xs">
            {system?.model_checkpoint || t('common.loading')}
          </span>
        </SettingsRow>
        <SettingsRow
          id="diagnostics-asr"
          title={
            <span className="inline-flex items-center gap-2">
              <Mic2Icon className="size-4 text-muted-foreground" aria-hidden="true" />
              {t('engineSidebar.asr')}
            </span>
          }
        >
          <span className="max-w-xl break-all text-right font-mono text-xs">
            {system?.asr_model || t('common.loading')}
          </span>
        </SettingsRow>
        <SettingsRow
          id="diagnostics-translator"
          title={
            <span className="inline-flex items-center gap-2">
              <LanguagesIcon className="size-4 text-muted-foreground" aria-hidden="true" />
              {t('about.translator')}
            </span>
          }
        >
          <span className="max-w-xl break-all text-right font-mono text-xs">
            {system?.translate_provider || t('common.loading')}
          </span>
        </SettingsRow>
        <SettingsRow
          id="diagnostics-data-directory"
          title={
            <span className="inline-flex items-center gap-2">
              <FolderOpenIcon className="size-4 text-muted-foreground" aria-hidden="true" />
              {t('settings.data_directory')}
            </span>
          }
        >
          <span className="max-w-xl break-all text-right font-mono text-xs">
            {system?.data_dir || t('common.loading')}
          </span>
        </SettingsRow>
        <SettingsRow id="diagnostics-outputs" title={t('settings.storage_child_outputs')}>
          <span className="max-w-xl break-all text-right font-mono text-xs">
            {system?.outputs_dir || t('common.loading')}
          </span>
        </SettingsRow>
      </SettingsSection>

      <ModelCredits />

      <SettingsSection icon={ActivityIcon} title={t('about.diagnostics')}>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <Button size="sm" disabled={action !== null} onClick={() => void runCheck()}>
            {action === 'check' ? (
              <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
            ) : (
              <ActivityIcon />
            )}
            {t('about.self_check')}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={action !== null}
            onClick={() => void buildBundle()}
          >
            {action === 'bundle' ? (
              <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
            ) : (
              <DownloadIcon />
            )}
            {t('about.save_bundle')}
          </Button>
          <Button size="sm" variant="ghost" disabled={action !== null} onClick={() => void copy()}>
            {action === 'copy' ? (
              <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
            ) : (
              <CopyIcon />
            )}
            {t('about.copy_diagnostics')}
          </Button>
        </div>

        {error && (
          <div className="p-4">
            <SettingsActionError
              title={t('about.self_check_failed', { message: '' }).trim()}
              detail={error}
              onDismiss={() => setError(null)}
            />
          </div>
        )}

        {report && (
          <div>
            <div className="flex flex-wrap items-center gap-3 px-4 py-3">
              {report.summary.ok ? (
                <CheckCircle2Icon className="size-5 text-success" aria-hidden="true" />
              ) : (
                <AlertTriangleIcon className="size-5 text-warning" aria-hidden="true" />
              )}
              <p className="text-sm font-medium">
                {t(report.summary.ok ? 'about.self_check_healthy' : 'about.self_check_attention', {
                  count: report.summary.failures,
                })}
              </p>
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {report.summary.passed} / {report.checks.length}
              </span>
            </div>
            <ul className="divide-y divide-border/50">
              {report.checks.map((check) => {
                const Icon =
                  check.status === 'ok'
                    ? CheckCircle2Icon
                    : check.status === 'warn'
                      ? AlertTriangleIcon
                      : XCircleIcon;
                const fix = FIX_ROUTES[check.id];
                return (
                  <li key={check.id} className="flex min-w-0 items-start gap-3 px-4 py-3">
                    <Icon
                      aria-hidden="true"
                      className={
                        'mt-0.5 size-4 shrink-0 ' +
                        (check.status === 'ok'
                          ? 'text-success'
                          : check.status === 'warn'
                            ? 'text-warning'
                            : 'text-destructive')
                      }
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-medium">{check.label}</h3>
                        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                          {t(`about.self_check_${check.status}`)}
                        </span>
                      </div>
                      <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">
                        {check.detail}
                      </p>
                      {check.hint && (
                        <p className="mt-1 break-words text-xs leading-5">{check.hint}</p>
                      )}
                    </div>
                    {check.status !== 'ok' && fix && (
                      <Button
                        render={<Link to={fix} />}
                        nativeButton={false}
                        size="xs"
                        variant="ghost"
                        className="shrink-0"
                      >
                        {t('common.open')}
                        <ExternalLinkIcon />
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </SettingsSection>
    </>
  );
}
