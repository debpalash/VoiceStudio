import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { DICTATION_SCRIPTS } from '@shared/utils/dictationScripts';
import { apiFetch, apiJson, apiPath, ApiError } from '@/lib/api/client';
import { Button } from './ui/button';
import { AudioPreviewButton } from './audio-preview-button';
import { beginAppActivity } from '@/lib/app-activity';
import { DictationSetup } from '@/features/transcriptions/dictation-setup';

export function DictationDemo() {
  const { t } = useTranslation();
  const [pending, setPending] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { text?: string; error?: string }>>({});
  const request = useRef<AbortController | null>(null);
  const assets = useQuery({
    queryKey: ['dictation-demo-assets'],
    queryFn: ({ signal }) =>
      apiFetch(DICTATION_SCRIPTS[0]!.wav, { method: 'HEAD', signal }).then(() => true),
    retry: false,
    staleTime: Infinity,
  });
  const readiness = useQuery({
    queryKey: ['transcription-readiness'],
    queryFn: ({ signal }) => apiJson<{ ready: boolean }>('/dictation/readiness', { signal }),
    refetchInterval: (query) => (query.state.data?.ready === false ? 15_000 : false),
  });
  useEffect(() => () => request.current?.abort(), []);
  const replay = async (script: (typeof DICTATION_SCRIPTS)[number]) => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const finishActivity = beginAppActivity('dictation');
    setPending(script.id);
    setResults((current) => ({ ...current, [script.id]: {} }));
    try {
      const audio = await apiFetch(script.wav, { signal: controller.signal });
      const body = new FormData();
      body.append('audio', await audio.blob(), script.id + '.wav');
      const result = await apiJson<{ text: string }>('/transcribe', {
        method: 'POST',
        body,
        signal: controller.signal,
      });
      if (!controller.signal.aborted)
        setResults((current) => ({
          ...current,
          [script.id]: { text: result.text },
        }));
    } catch (error) {
      if (!controller.signal.aborted) {
        if (error instanceof ApiError && error.status === 409) void readiness.refetch();
        setResults((current) => ({
          ...current,
          [script.id]: {
            error:
              error instanceof ApiError && error.status === 409
                ? 'asr_missing.message'
                : 'transcriptions.failed',
          },
        }));
      }
    } finally {
      finishActivity();
      request.current = null;
      if (!controller.signal.aborted) setPending(null);
    }
  };
  if (!readiness.isPending && !readiness.data?.ready) {
    return (
      <section className="space-y-3">
        <h2 className="text-sm font-medium">{t('demo.dictation_title')}</h2>
        <p className="text-sm text-muted-foreground">{t('demo.dictation_lede')}</p>
        <DictationSetup onReady={() => void readiness.refetch()} />
      </section>
    );
  }
  if (!assets.data) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">{t('demo.dictation_title')}</h2>
      <p className="text-sm text-muted-foreground">{t('demo.dictation_lede')}</p>
      <div className="space-y-2">
        {DICTATION_SCRIPTS.map((script) => (
          <article key={script.id} className="space-y-3 rounded-xl border border-border/60 p-4">
            <h3 className="text-sm font-medium">{t(script.labelKey)}</h3>
            <p className="text-sm leading-relaxed text-muted-foreground">{script.text}</p>
            <div className="flex items-center gap-2">
              <AudioPreviewButton
                src={apiPath(script.wav)}
                source={'dictation-demo:' + script.id}
              />
              <Button
                size="sm"
                variant="outline"
                disabled={pending !== null}
                onClick={() => void replay(script)}
                aria-label={t('demo.aria_replay', {
                  label: t(script.labelKey),
                })}
              >
                {t(pending === script.id ? 'demo.dictation_transcribing' : 'demo.dictation_replay')}
              </Button>
            </div>
            {results[script.id]?.text && (
              <p role="status" className="text-sm text-success">
                {results[script.id].text}
              </p>
            )}
            {results[script.id]?.error && (
              <p role="alert" className="text-sm text-destructive">
                {t(results[script.id].error!)}
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
