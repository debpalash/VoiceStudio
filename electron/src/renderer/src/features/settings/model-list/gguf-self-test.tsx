import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LoaderCircleIcon, Volume2Icon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { GGUF_ENGINE } from './gguf-quant-picker';

export interface SelfTestResult {
  ok: boolean;
  message: string;
  duration_ms: number;
  sample_rate?: number | null;
  audio_seconds?: number | null;
  timed_out?: boolean;
  runtime_device?: string | null;
  runtime_log?: string | null;
}

/** A short real synthesis on OmniVoice GGUF, naming the Vulkan device it used. */
export function GgufSelfTest() {
  const { t } = useTranslation();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SelfTestResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setResult(null);
    setFailed(null);
    try {
      setResult(
        await apiJson<SelfTestResult>(`/engines/${GGUF_ENGINE}/selftest`, { method: 'POST' }),
      );
    } catch (error) {
      setResult(null);
      setFailed(describeError(error));
    } finally {
      setRunning(false);
    }
  };

  const detail =
    [result?.message, result?.runtime_log, failed].filter(Boolean).join('\n') || undefined;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      <Button size="sm" variant="ghost" disabled={running} onClick={() => void run()}>
        {running ? <LoaderCircleIcon className="animate-spin" /> : <Volume2Icon />}
        {t(running ? 'engines.selfTesting' : 'engines.selfTest')}
      </Button>
      {result && (
        <span
          role="status"
          title={detail}
          className={
            result.ok
              ? 'min-w-0 truncate text-xs font-medium text-emerald-500'
              : 'min-w-0 truncate text-xs font-medium text-destructive'
          }
        >
          {result.ok
            ? t('engines.selfTestOk', {
                seconds: Number(result.audio_seconds ?? 0).toFixed(2),
                khz: result.sample_rate ? Math.round(result.sample_rate / 1000) : '?',
                took: `${(result.duration_ms / 1000).toFixed(1)} s`,
              })
            : t(result.timed_out ? 'engines.selfTestTimedOut' : 'engines.selfTestFailed')}
          {result.ok && result.runtime_device ? ` · ${result.runtime_device}` : null}
        </span>
      )}
      {failed && (
        <span role="alert" className="text-xs font-medium text-destructive">
          {t('engines.selfTestUnreachable')}
        </span>
      )}
      {detail && (
        <details className="basis-full text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">{t('common.details')}</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]">
            {detail}
          </pre>
        </details>
      )}
    </div>
  );
}
