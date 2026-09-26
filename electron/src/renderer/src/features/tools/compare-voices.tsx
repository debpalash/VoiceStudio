import { useComparison, setComparison } from './comparison-state';
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { EngineNotice } from '@/components/engine-notice';
import { Input } from '@/components/ui/input';
import { PipelineFailure } from '@/components/pipeline-failure';
import { WaveformPlayer } from '@/components/waveform-player';
import { useProfiles } from '@/hooks/use-profiles';
import { generateClone } from '@/lib/api/generate';
import { cloneSettingsStore } from '@/lib/store/clone-settings';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { queryKeys } from '@/lib/query';
import { describeError } from '@/lib/api/client';
import { PRESETS } from '@shared/utils/constants';
import { beginAppActivity } from '@/lib/app-activity';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';

export function CompareVoices() {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const ttsBlocker = useTtsReadiness();
  const client = useQueryClient();
  const { text, voices, searches, urls } = useComparison();
  const setText = (value: string) => setComparison('text', value);
  const setVoices = (value: (current: string[]) => string[]) => setComparison('voices', value);
  const setSearches = (value: (current: string[]) => string[]) => setComparison('searches', value);
  const setUrls = (value: string[] | ((current: string[]) => string[])) =>
    setComparison('urls', value);
  const [stage, setStage] = useState<number | null>(null);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const availableProfiles = Array.isArray(profiles.data) ? profiles.data : [];
  useEffect(
    () => () => {
      request.current?.abort();
    },
    [],
  );
  const options = [
    ...availableProfiles.map((profile) => ({
      id: profile.id,
      name: profile.name,
      profileId: profile.id,
      instruct: profile.instruct || '',
      language: profile.language || 'Auto',
      seed: profile.seed ?? undefined,
    })),
    ...PRESETS.map((preset) => ({
      id: 'preset:' + preset.id,
      name: preset.name + ' ' + t('compare.preset_suffix'),
      profileId: undefined,
      instruct: Object.values(preset.attrs)
        .filter((value) => value !== 'Auto')
        .join(', '),
      language: 'Auto',
      seed: undefined,
    })),
  ];
  const selected = voices.map((id) => options.find((option) => option.id === id));
  const run = async () => {
    if (request.current || !text.trim() || selected.some((voice) => !voice) || ttsBlocker) return;
    const release = acquireSynthesis();
    if (!release) {
      setError(t('tts_errors.generation_in_progress'));
      return;
    }
    const controller = new AbortController();
    const finishActivity = beginAppActivity('synthesis');
    request.current = controller;
    const settings = cloneSettingsStore.state;
    setError('');
    setStage(0);
    setUrls(['', '']);
    try {
      for (let side = 0; side < 2; side++) {
        if (controller.signal.aborted) break;
        setStage(side);
        const voice = selected[side]!;
        const result = await generateClone(
          {
            ...settings,
            text,
            profileId: voice.profileId,
            instruct: voice.instruct,
            language: voice.language,
            seed: voice.seed,
          },
          { signal: controller.signal },
        );
        if (controller.signal.aborted) break;
        const url = URL.createObjectURL(result.blob);
        setUrls((current) => current.map((old, index) => (index === side ? url : old)));
        void client.invalidateQueries({ queryKey: queryKeys.history });
      }
    } catch (error) {
      if (!controller.signal.aborted) setError(describeError(error));
    } finally {
      finishActivity();
      request.current = null;
      release();
      if (!controller.signal.aborted) setStage(null);
    }
  };
  const busy = stage !== null;
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <h2 className="text-lg font-semibold">{t('compare.title')}</h2>
      <textarea
        aria-label={t('compare.test_phrase')}
        value={text}
        disabled={busy}
        onChange={(event) => setText(event.target.value)}
        rows={4}
        className="w-full resize-y rounded-lg border border-input bg-transparent p-3 text-sm leading-6 outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <div className="grid grid-cols-2 gap-5 max-md:grid-cols-1">
        {[0, 1].map((side) => (
          <section key={side} className="min-w-0 space-y-3">
            <h3 className="text-sm font-medium">
              {t(side === 0 ? 'compare.voice_a' : 'compare.voice_b')}
            </h3>
            <Input
              aria-label={
                t('compare.select_voice') +
                ' ' +
                t(side === 0 ? 'compare.voice_a' : 'compare.voice_b')
              }
              placeholder={t('preferences.search')}
              value={searches[side]}
              disabled={busy}
              onChange={(event) =>
                setSearches((current) =>
                  current.map((value, index) => (index === side ? event.target.value : value)),
                )
              }
            />
            <div className="max-h-44 space-y-1 overflow-y-auto rounded-lg border border-border/50 p-1">
              {options
                .filter((option) =>
                  option.name.toLowerCase().includes(searches[side].toLowerCase()),
                )
                .map((option) => (
                  <Button
                    key={option.id}
                    type="button"
                    variant={voices[side] === option.id ? 'secondary' : 'ghost'}
                    aria-pressed={voices[side] === option.id}
                    disabled={busy}
                    className="w-full justify-start truncate"
                    onClick={() => {
                      setVoices((current) =>
                        current.map((value, index) => (index === side ? option.id : value)),
                      );
                      setUrls((current) =>
                        current.map((value, index) => (index === side ? '' : value)),
                      );
                    }}
                  >
                    {option.name}
                  </Button>
                ))}
            </div>
            <p className="truncate text-sm text-muted-foreground">
              {selected[side]?.name || t('compare.select_voice')}
            </p>
            {urls[side] ? (
              <WaveformPlayer
                key={urls[side]}
                src={urls[side]}
                source={'compare-' + side}
                height={48}
              />
            ) : (
              <p className="text-xs text-muted-foreground">{t('compare.no_audio')}</p>
            )}
          </section>
        ))}
      </div>
      <Button
        type="button"
        disabled={busy || !text.trim() || selected.some((voice) => !voice) || ttsBlocker !== null}
        onClick={() => void run()}
      >
        {t(
          busy
            ? stage === 0
              ? 'compare.generating_voice_a'
              : 'compare.generating_voice_b'
            : 'compare.compare_btn',
        )}
      </Button>
      {ttsBlocker === 'engine' && <EngineNotice operation="compare" compact />}
      {ttsBlocker === 'loading' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('preferences.loading')}
        </p>
      )}
      {error && (
        <PipelineFailure
          fallback={error || t('profileIdentity.generation_failed')}
          onDismiss={() => setError('')}
        />
      )}
    </div>
  );
}
