import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIcon, LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { useEngines } from '@/hooks/use-engines';
import { useEngineSelect } from '@/features/settings/model-list/use-engine-select';
// @ts-expect-error Shared JSX component has no declaration file.
import SearchableSelect from '@shared/components/SearchableSelect';

interface SelfTestResult {
  ok: boolean;
  message: string;
  duration_ms: number;
  runtime_device?: string | null;
  runtime_log?: string | null;
}

/** Pick an installed TTS runtime and run a real GGUF synthesis diagnostic. */
export function CloneEnginePicker({ disabled = false }: { disabled?: boolean }) {
  const { t } = useTranslation();
  const engines = useEngines();
  const selection = useEngineSelect('tts');
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<SelfTestResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const data = engines.data?.tts;
  const available = data?.backends.filter((engine) => engine.available) ?? [];
  const active = data?.active ?? '';
  const activeEngine = available.find((engine) => engine.id === active);
  const canRunTest = Boolean(
    activeEngine &&
      (active === 'omnivoice-gguf' || activeEngine.isolation_mode !== 'subprocess'),
  );

  const test = async () => {
    if (!canRunTest || testing) return;
    setTesting(true);
    setFailure(null);
    setResult(null);
    try {
      const next = await apiJson<SelfTestResult>(`/engines/${encodeURIComponent(active)}/selftest`, {
        method: 'POST',
      });
      setResult(next);
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-1.5">
      <div className="w-60 min-w-0 max-w-full shrink">
        <SearchableSelect
          options={available.map((engine) => ({ value: engine.id, label: engine.display_name }))}
          value={active}
          onChange={(engineId: string) => {
            if (engineId !== active) {
              setResult(null);
              setFailure(null);
              void selection.select(engineId);
            }
          }}
          ariaLabel={t('engineLabel')}
          placeholder={t('engineLabel')}
          size="sm"
          buttonClassName="min-w-0 max-w-full truncate rounded-md border border-border/60 bg-background px-2 py-1 text-xs"
          menuPortal
          disabled={disabled || !data || data.env_override || selection.busy}
        />
      </div>
      {canRunTest && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-8 gap-1 px-2 text-xs"
          disabled={disabled || testing}
          onClick={() => void test()}
          title={result?.runtime_log || failure || undefined}
        >
          {testing ? <LoaderCircleIcon className="animate-spin" /> : <ActivityIcon />}
          {t(testing ? 'engines.testing' : 'engines.testEngine')}
        </Button>
      )}
      {(result || failure) && (
        <span
          role={failure || !result?.ok ? 'alert' : 'status'}
          className={`max-w-56 truncate text-xs ${failure || !result?.ok ? 'text-destructive' : 'text-muted-foreground'}`}
          title={failure || result?.runtime_log || result?.message}
        >
          {failure ||
            (result?.ok
              ? [
                  t('engines.latencyMs', { ms: Math.round(result.duration_ms) }),
                  result.runtime_device,
                ]
                  .filter(Boolean)
                  .join(' · ')
              : result?.message)}
        </span>
      )}
    </div>
  );
}
