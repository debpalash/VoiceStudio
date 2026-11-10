import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AudioLinesIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DisabledReason, firstReason } from '@/components/disabled-reason';
import { PipelineFailure } from '@/components/pipeline-failure';
import { WaveformPlayer } from '@/components/waveform-player';
import { SaveAudioButton } from '@/components/save-audio-button';
import { EngineInstall } from '@/features/settings/engine-install';
import { apiPath, describeError } from '@/lib/api/client';
import {
  fetchSfxStatus,
  generateSfx,
  sfxErrorKey,
  sfxReasonKey,
  SFX_ENGINE_ID,
  type SfxResult,
  type SfxStatus,
} from '@/lib/api/sfx';
import { queryKeys } from '@/lib/query';

const SFX_STATUS_KEY = ['sfx-status'] as const;

export function SoundEffects() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const status = useQuery<SfxStatus>({
    queryKey: SFX_STATUS_KEY,
    queryFn: ({ signal }) => fetchSfxStatus(signal),
    // While the install runs the page keeps asking, so Generate lights up by itself.
    refetchInterval: (query) =>
      query.state.data && !query.state.data.available && query.state.data.supported ? 5000 : false,
  });
  const info = status.data;
  const [prompt, setPrompt] = useState('');
  const [seconds, setSeconds] = useState<number | null>(null);
  const [result, setResult] = useState<SfxResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const duration = seconds ?? info?.default_seconds ?? 10;
  const limits = {
    min: info?.min_seconds ?? 1,
    max: info?.max_seconds ?? 30,
    max_chars: info?.max_prompt_chars ?? 500,
  };
  const blocked = firstReason([
    [Boolean(info && !info.available), t(sfxReasonKey(info?.reason_code))],
    [!prompt.trim(), t('sfx.prompt_required')],
  ]);
  const run = async () => {
    if (!info || blocked || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const made = await generateSfx(prompt.trim(), duration, controller.signal);
      if (!controller.signal.aborted) {
        setResult(made);
        void client.invalidateQueries({ queryKey: queryKeys.history });
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(t(sfxErrorKey(e), limits));
    } finally {
      if (request.current === controller) {
        request.current = null;
        setBusy(false);
      }
    }
  };
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <h2 className="text-lg font-semibold">{t('sfx.title')}</h2>
      {status.isError && !info && (
        <PipelineFailure
          fallback={describeError(status.error)}
          action={
            <Button variant="outline" size="sm" onClick={() => void status.refetch()}>
              {t('common.retry')}
            </Button>
          }
        />
      )}
      {info && !info.supported && (
        <div role="status" className="space-y-1 text-sm">
          <p className="font-medium">{t('sfx.unsupported_title')}</p>
          <p className="text-muted-foreground">{t(sfxReasonKey(info.reason_code))}</p>
        </div>
      )}
      {info && info.supported && !info.installed && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <div className="space-y-1">
            <p className="font-medium">{t('sfx.install_title')}</p>
            <p className="text-muted-foreground">
              {t('sfx.install_hint', {
                size: (info.download_bytes / 1024 ** 3).toFixed(1),
                license: info.license,
              })}
            </p>
          </div>
          <EngineInstall id={SFX_ENGINE_ID} />
        </div>
      )}
      <label className="block space-y-2 text-sm">
        <span>{t('sfx.prompt_label')}</span>
        <textarea
          dir="auto"
          className="min-h-28 w-full resize-y rounded-lg border border-input bg-transparent p-3 leading-7 outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
          disabled={busy}
          maxLength={info?.max_prompt_chars}
          placeholder={t('sfx.prompt_placeholder')}
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
            setResult(null);
          }}
        />
      </label>
      <label className="block space-y-2 text-sm">
        <span>{t('sfx.duration', { seconds: duration })}</span>
        <input
          type="range"
          className="w-full accent-primary"
          aria-label={t('sfx.duration', { seconds: duration })}
          min={info?.min_seconds ?? 1}
          max={info?.max_seconds ?? 30}
          step={1}
          value={duration}
          disabled={busy}
          onChange={(event) => {
            setSeconds(Number(event.target.value));
            setResult(null);
          }}
        />
      </label>
      {error && <PipelineFailure fallback={error} onDismiss={() => setError('')} />}
      <div className="flex items-center gap-2">
        <DisabledReason reason={blocked}>
          {(describedBy) => (
            <Button
              className="min-w-48"
              disabled={!info || Boolean(blocked) || busy}
              aria-describedby={describedBy}
              onClick={() => void run()}
            >
              <AudioLinesIcon />
              {busy ? t('sfx.generating') : t('sfx.generate')}
            </Button>
          )}
        </DisabledReason>
        {busy && (
          <Button variant="ghost" onClick={() => request.current?.abort()}>
            {t('common.cancel')}
          </Button>
        )}
      </div>
      {result && (
        <section className="space-y-3 border-t pt-4" aria-label={t('sfx.result')}>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium">{t('sfx.result')}</h3>
            <SaveAudioButton
              url={apiPath(result.audio_url)}
              suggestedName={`voicestudio-sfx-${result.id}.wav`}
            />
          </div>
          <WaveformPlayer src={apiPath(result.audio_url)} source="sfx-result" height={40} />
        </section>
      )}
    </div>
  );
}
