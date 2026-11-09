import { useConversion, setConversion } from './conversion-state';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowLeftRightIcon, MicIcon, SquareIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { LanguagePicker } from '@/features/clone/language-picker';
import { CONVERT_SOURCE_LANGUAGES } from './convert-languages';
import { PipelineFailure } from '@/components/pipeline-failure';
import { AgentFixButton } from '@/components/agent-fix-button';
import { ProfileAvatar } from '@/components/profile-avatar';
import { WaveformPlayer } from '@/components/waveform-player';
import { SaveAudioButton } from '@/components/save-audio-button';
import { useEngines } from '@/hooks/use-engines';
import { useProfiles } from '@/hooks/use-profiles';
import { useRecording } from '@/hooks/use-recording';
import { ApiError, apiPath, describeError } from '@/lib/api/client';
import { convertSpeech, type ConvertResult } from '@/lib/api/convert';
import { queryKeys } from '@/lib/query';
import { beginAppActivity } from '@/lib/app-activity';

export function ConvertVoice() {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const engines = useEngines();
  const canClone = engines.activeTtsReady && engines.activeTts?.supports_cloning === true;
  // Keep my delivery (#3421) runs on any installed engine that converts speech
  // to speech, not only the active one.
  const canKeepDelivery = (engines.data?.tts?.backends ?? []).some(
    (b) =>
      b.available && b.supports_voice_conversion === true && b.routing_status !== 'unavailable',
  );
  const client = useQueryClient();
  const { file, voice, search, match, keepDelivery, language, result } = useConversion();
  const ready = keepDelivery ? canKeepDelivery : canClone;
  const setFile = (value: File | null) => setConversion('file', value);
  const setVoice = (value: string) => setConversion('voice', value);
  const setSearch = (value: string) => setConversion('search', value);
  const setMatch = (value: boolean) => setConversion('match', value);
  const setKeepDelivery = (value: boolean) => setConversion('keepDelivery', value);
  const setLanguage = (value: string) => setConversion('language', value);
  const setResult = (value: ConvertResult | null) => setConversion('result', value);
  const [sourceUrl, setSourceUrl] = useState('');
  const [error, setError] = useState('');
  const [needsModel, setNeedsModel] = useState(false);
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const invalidate = () => {
    request.current?.abort();
    request.current = null;
    setBusy(false);
    setResult(null);
    setError('');
    setNeedsModel(false);
  };
  const ingest = (next: File) => {
    if (
      !next.size ||
      !(next.type.startsWith('audio/') || /\.(mp3|wav|m4a|flac|ogg|aac|webm)$/i.test(next.name))
    ) {
      setError(t('clone.unsupported_audio'));
      return;
    }
    invalidate();
    setFile(next);
  };
  const recording = useRecording(ingest, false);
  const recordingBusy = recording.isStarting || recording.isRecording || recording.isCleaning;
  useEffect(() => {
    const url = file ? URL.createObjectURL(file) : '';
    setSourceUrl(url);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [file]);
  useEffect(() => () => request.current?.abort(), []);
  // Match the backend's profile conditioning: designs use their saved sample
  // or instructions; clones need a reference (a locked take takes precedence).
  // Keep my delivery converts into a recording, so a design without a saved
  // sample has nothing to convert into.
  const voices = (profiles.data ?? []).filter(
    (p) =>
      (!keepDelivery && p.kind === 'design') ||
      p.ref_audio_path ||
      (p.is_locked && p.locked_audio_path),
  );
  const valid = ready && file && voices.some((p) => p.id === voice) && !recordingBusy;
  const run = async () => {
    if (!valid || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const finishTranscription = beginAppActivity('transcription');
    const finishSynthesis = beginAppActivity('synthesis');
    setBusy(true);
    setError('');
    setNeedsModel(false);
    setResult(null);
    try {
      const converted = await convertSpeech(
        file,
        voice,
        match,
        controller.signal,
        language,
        keepDelivery,
      );
      if (!controller.signal.aborted) {
        setResult(converted);
        void client.invalidateQueries({ queryKey: queryKeys.history });
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        const detail = e instanceof ApiError ? e.payload?.detail : null;
        const code =
          detail && typeof detail === 'object' && 'error' in detail ? detail.error : null;
        setNeedsModel(code === 'asr_model_missing' || code === 'voice_conversion_engine_missing');
        setError(describeError(e));
      }
    } finally {
      finishTranscription();
      finishSynthesis();
      if (request.current === controller) {
        request.current = null;
        setBusy(false);
      }
    }
  };
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold">{t('convert.convert')}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t('convert.need_source_and_voice')}</p>
      </div>
      <section className="space-y-3" aria-label={t('convert.source_kicker')}>
        <h3 className="text-sm font-medium">{t('convert.source_kicker')}</h3>
        <input
          ref={input}
          type="file"
          accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac,.webm"
          className="sr-only"
          aria-label={t('convert.source_kicker')}
          onChange={(e) => {
            const chosen = e.target.files?.[0];
            if (chosen) ingest(chosen);
            e.target.value = '';
          }}
          disabled={recordingBusy}
        />
        <Button
          variant="outline"
          className="h-auto min-h-20 w-full whitespace-normal border-dashed p-4"
          disabled={recordingBusy}
          onClick={() => input.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (!recordingBusy && e.dataTransfer.files[0]) ingest(e.dataTransfer.files[0]);
          }}
        >
          {file?.name ?? t('convert.drop_audio')}
        </Button>
        {sourceUrl && <WaveformPlayer src={sourceUrl} source="convert-source" height={36} />}
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            disabled={recording.isStarting || recording.isCleaning}
            onClick={() => {
              if (recording.isRecording) recording.stop();
              else {
                invalidate();
                void recording.start();
              }
            }}
          >
            {recording.isRecording ? <SquareIcon /> : <MicIcon />}
            {recording.isRecording ? t('clone.stop_recording') : t('clone.record')}
          </Button>
          {file && (
            <Button
              variant="ghost"
              onClick={() => {
                invalidate();
                setFile(null);
              }}
            >
              {t('clone.clear')}
            </Button>
          )}
        </div>
      </section>
      <section className="space-y-3" aria-label={t('convert.target_voice')}>
        <h3 className="text-sm font-medium">{t('convert.target_voice')}</h3>
        <Input
          aria-label={t('convert.pick_voice')}
          placeholder={t('convert.pick_voice')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="max-h-48 space-y-1 overflow-y-auto">
          {voices
            .filter((p) => p.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
            .map((p) => (
              <Button
                key={p.id}
                variant={voice === p.id ? 'secondary' : 'ghost'}
                className="w-full justify-start"
                aria-pressed={voice === p.id}
                onClick={() => {
                  invalidate();
                  setVoice(p.id);
                }}
              >
                <ProfileAvatar name={p.name} imageUrl={p.image_url} className="size-6" />
                {p.name}
              </Button>
            ))}
          {!voices.length && (
            <p className="text-sm text-muted-foreground">
              {keepDelivery && profiles.data?.length
                ? t('convert.keep_delivery_no_voices')
                : t('clone.no_profiles')}
            </p>
          )}
        </div>
      </section>
      <div className="space-y-1">
        <label className="flex items-center gap-3 text-sm">
          <Switch
            checked={keepDelivery}
            disabled={busy}
            onCheckedChange={(value) => {
              invalidate();
              setKeepDelivery(value);
            }}
          />
          {t('convert.keep_delivery')}
        </label>
        <p className="text-xs text-muted-foreground">{t('convert.keep_delivery_hint')}</p>
      </div>
      {!keepDelivery && (
        <div className="space-y-2 text-sm">
          <p>{t('convert.source_language')}</p>
          <div>
            <LanguagePicker
              value={language}
              supportedOptions={CONVERT_SOURCE_LANGUAGES}
              recentsScope="convert-source-language"
              disabled={busy}
              onValueChange={(value) => {
                invalidate();
                setLanguage(value);
              }}
            />
          </div>
        </div>
      )}
      {!keepDelivery && (
        <div className="space-y-1">
          <label className="flex items-center gap-3 text-sm">
            <Switch
              checked={match}
              onCheckedChange={(value) => {
                invalidate();
                setMatch(value);
              }}
            />
            {t('convert.match_duration')}
          </label>
          <p className="text-xs text-muted-foreground">{t('convert.match_duration_hint')}</p>
        </div>
      )}
      {engines.isError && !engines.data && !busy && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
        >
          <span>{describeError(engines.error)}</span>
          <Button variant="outline" size="sm" onClick={engines.retry}>
            {t('common.retry')}
          </Button>
        </div>
      )}
      {!ready && !(engines.isError && !engines.data) && !busy && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
        >
          <span>
            {keepDelivery
              ? t('convert.keep_delivery_engine_required')
              : t('convert.cloning_required')}
          </span>
          <Link
            to="/settings/models/$family"
            params={{ family: 'tts' }}
            className="font-medium text-primary hover:underline"
          >
            {t('modelSettings.models')}
          </Link>
        </div>
      )}
      {error && (
        <PipelineFailure
          fallback={error}
          onDismiss={() => setError('')}
          action={
            needsModel ? (
              <div className="flex shrink-0 items-center gap-2">
                <Link
                  to="/settings/models/$family"
                  params={{ family: keepDelivery ? 'tts' : 'asr' }}
                  className="text-xs font-medium text-primary hover:underline"
                >
                  {t('modelSettings.models')}
                </Link>
                <AgentFixButton
                  request={
                    keepDelivery
                      ? `Install CosyVoice 3 so Voice Conversion can keep the speaker's delivery, then keep the current source and target voice available for retry. Conversion reported: ${error}`
                      : `Restore local ASR readiness for Voice Conversion, then keep the current source and target voice available for retry. Conversion reported: ${error}`
                  }
                />
              </div>
            ) : undefined
          }
        />
      )}
      <div className="flex items-center gap-2">
        <Button className="min-w-48" disabled={!valid || busy} onClick={() => void run()}>
          <ArrowLeftRightIcon />
          {busy ? t('convert.converting') : t('convert.convert')}
        </Button>
        {busy && (
          <Button variant="ghost" onClick={invalidate}>
            {t('common.cancel')}
          </Button>
        )}
      </div>
      {result && (
        <section className="space-y-3 border-t pt-4" aria-label={t('convert.result_kicker')}>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium">{t('convert.result_kicker')}</h3>
            <SaveAudioButton
              url={apiPath(result.audio_url)}
              suggestedName={`voicestudio-${result.id}.wav`}
            />
          </div>
          <WaveformPlayer src={apiPath(result.audio_url)} source="convert-result" height={40} />
          {result.text && (
            <p className="text-sm">
              <span className="text-muted-foreground">{t('convert.transcript')}: </span>
              {result.text}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
