import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { DownloadIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { SettingsActionError } from '../settings-action-error';

export const GGUF_ENGINE = 'omnivoice-gguf';
const PATH = '/engines/omnivoice-gguf/quants';
const RUNTIME_PROBLEMS = new Set(['missing', 'placeholder', 'integrity', 'quarantined', 'other']);

export interface GgufQuantStatus {
  runtime: { ready: boolean; problem: string | null; vulkan_module: boolean };
  hardware: {
    backend: 'vulkan' | 'cpu' | 'unverified';
    device: string | null;
    gpu_name: string | null;
    shared_memory: boolean;
    native_bf16?: boolean | null;
    effective_vram_gb: number | null;
  };
  recommended: string;
  selection: string;
  selection_source: 'env' | 'settings' | 'auto';
  selection_valid?: boolean;
  env_var: string;
  selected: string;
  in_use: string | null;
  options: Array<{
    quant: string;
    tag: string;
    approx_size_mb: number;
    installed: boolean;
    recommended: boolean;
  }>;
  download: {
    state: 'idle' | 'running' | 'done' | 'failed';
    quant: string | null;
    error: string | null;
    bytes_total: number;
    bytes_done?: number;
  };
}

const mb = (bytes: number) => Math.round(bytes / (1024 * 1024));

/** Quant choice and weight download for the OmniVoice GGUF engine row. */
export function GgufQuantPicker() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const status = useQuery({
    queryKey: ['gguf-quants'],
    queryFn: () => apiJson<GgufQuantStatus>(PATH),
    refetchInterval: (query) => (query.state.data?.download.state === 'running' ? 1500 : false),
  });
  const data = status.data;
  if (!data) return null;
  const tagOf = (quant: string) => data.options.find((o) => o.quant === quant)?.tag ?? quant;
  const target =
    data.hardware.backend === 'vulkan'
      ? t('ggufQuant.target_vulkan', {
          device: data.hardware.gpu_name ?? data.hardware.device ?? '',
        })
      : t(data.hardware.backend === 'cpu' ? 'ggufQuant.target_cpu' : 'ggufQuant.target_unverified');
  const selected = data.options.find((o) => o.quant === data.selected);
  const job = data.download;
  const running = busy || job.state === 'running';

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setFailed(null);
    setDismissed(false);
    try {
      await action();
      await status.refetch();
      void client.invalidateQueries({ queryKey: queryKeys.engines });
    } catch (error) {
      setFailed(describeError(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex w-full flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          items={[
            {
              value: 'auto',
              label: t('ggufQuant.auto', { quant: tagOf(data.recommended), target }),
            },
            ...data.options.map((o) => ({ value: o.quant, label: o.tag })),
          ]}
          value={data.selection_source === 'settings' ? data.selection : 'auto'}
          disabled={running || data.selection_source === 'env'}
          onValueChange={(value) => {
            if (typeof value !== 'string') return;
            void run(() =>
              apiJson(`${PATH}/select`, { method: 'POST', body: JSON.stringify({ quant: value }) }),
            );
          }}
        >
          <SelectTrigger aria-label={t('ggufQuant.label')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">
              {t('ggufQuant.auto', { quant: tagOf(data.recommended), target })}
            </SelectItem>
            {data.options.map((o) => (
              <SelectItem key={o.quant} value={o.quant}>
                {t(o.installed ? 'ggufQuant.option_installed' : 'ggufQuant.option', {
                  quant: o.tag,
                  size: o.approx_size_mb,
                })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selected && !selected.installed && (
          <Button
            size="sm"
            variant="outline"
            disabled={running}
            onClick={() =>
              void run(() =>
                apiJson(`${PATH}/download`, {
                  method: 'POST',
                  body: JSON.stringify({ quant: selected.quant }),
                }),
              )
            }
          >
            <DownloadIcon />
            {t('ggufQuant.download', { quant: selected.tag, size: selected.approx_size_mb })}
          </Button>
        )}
      </div>
      {data.selection_source === 'env' && (
        <p className="text-xs text-muted-foreground">
          {t('ggufQuant.env_pinned', { env: data.env_var, quant: tagOf(data.selected) })}
        </p>
      )}
      {job.state === 'running' && (
        <p className="text-xs tabular-nums text-muted-foreground">
          {t('ggufQuant.downloading', {
            quant: tagOf(job.quant ?? ''),
            done: mb(job.bytes_done ?? 0),
            total: mb(job.bytes_total),
          })}
        </p>
      )}
      {data.in_use && data.in_use !== data.selected && (
        <p className="text-xs text-muted-foreground">
          {t('ggufQuant.using_installed', { quant: tagOf(data.in_use) })}
        </p>
      )}
      {!data.runtime.ready && RUNTIME_PROBLEMS.has(data.runtime.problem ?? '') && (
        <p className="text-xs text-destructive">{t('ggufQuant.runtime_' + data.runtime.problem)}</p>
      )}
      {data.runtime.ready && !data.in_use && !selected?.installed && (
        <p className="text-xs text-muted-foreground">{t('ggufQuant.weights_missing')}</p>
      )}
      {!dismissed && (failed || job.state === 'failed') && (
        <SettingsActionError
          className="max-w-sm text-left"
          title={t('ggufQuant.download_failed')}
          detail={failed || job.error || ''}
          onDismiss={() => setDismissed(true)}
        />
      )}
    </div>
  );
}
