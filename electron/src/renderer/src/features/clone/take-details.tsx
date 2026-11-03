import { SaveTakeProfile } from './save-take-profile';
import { LockVoice } from './lock-voice';
import { useTranslation } from 'react-i18next';
import {
  AudioLinesIcon,
  Clock3Icon,
  FingerprintIcon,
  RepeatIcon,
  SlidersHorizontalIcon,
  WandSparklesIcon,
} from 'lucide-react';
import { useProfiles } from '@/hooks/use-profiles';
import { Button } from '@/components/ui/button';
import { ProfileAvatar } from '@/components/profile-avatar';
import { SaveAudioButton } from '@/components/save-audio-button';
import { WaveformPlayer } from '@/components/waveform-player';
import { audioMp3Url, audioUrl } from '@/lib/api/client';
import type { HistoryItem } from '@/lib/api/types';
import { reuseTake, takeSettings } from '@/lib/store/takes';
import { useGenerateClone } from '@/hooks/use-generate';
import { useNavigate } from '@tanstack/react-router';
import { designDraftFromTake, writeDraft } from '@/features/design/design-draft';
import { openTake } from '@/lib/store/takes';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { languageOptions } from '@/lib/language-options';
import { formatRelative, formatSeconds } from './format';

const NUMERIC_SETTINGS = [
  ['steps', 'clone.steps'],
  ['cfg', 'clone.cfg'],
  ['speed', 'clone.speed'],
  ['tShift', 'clone.tshift'],
  ['posTemp', 'clone.pos_temp'],
  ['classTemp', 'clone.class_temp'],
  ['layerPenalty', 'clone.layer_pen'],
] as const;

/** Two language settings name the same choice ("Auto" and an unset take are both Auto). */
export function sameLanguage(left: string | null | undefined, right: string | null | undefined) {
  const normal = (value: string | null | undefined) => (value?.trim() || 'Auto').toLowerCase();
  return normal(left) === normal(right);
}

export function TakeDetails({ item }: { item: HistoryItem }) {
  const { t, i18n } = useTranslation();
  const profiles = useProfiles();
  const navigate = useNavigate();
  const { isGenerating } = useGenerateClone();
  const settings = takeSettings(item);
  // The composer's language drives the next take; this take keeps the one it used.
  const composerLanguage = useCloneSetting('language');
  const languageLabel = (value: string | null | undefined) =>
    languageOptions([value?.trim() || 'Auto'], i18n.language, t('clone.auto'))[0]?.label ??
    (value || t('clone.auto'));
  const takeLanguage = languageLabel(item.language);
  const voice = profiles.data?.find((profile) => profile.id === item.profile_id);
  const isDesign = item.mode === 'design';
  const ModeIcon = isDesign ? WandSparklesIcon : FingerprintIcon;
  const numericSettings = NUMERIC_SETTINGS.filter(([key]) => settings[key] !== undefined);
  const hasRecipe =
    numericSettings.length > 0 ||
    settings.duration !== undefined ||
    settings.denoise !== undefined ||
    settings.postprocess !== undefined;
  const reuse = async () => {
    if (isDesign) {
      writeDraft(designDraftFromTake(item));
      setCloneSetting('language', item.language || 'Auto');
      openTake(null);
      await navigate({ to: '/design' });
      return;
    }
    await reuseTake(item);
    await navigate({ to: '/clone' });
  };
  return (
    <div className="flex flex-col gap-5">
      <section className="flex min-w-0 items-center gap-3 rounded-xl border border-border/60 bg-muted/20 p-3">
        {voice ? (
          <ProfileAvatar name={voice.name} imageUrl={voice.image_url} className="size-10" />
        ) : (
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <ModeIcon className="size-4" aria-hidden="true" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <ModeIcon className="size-3.5 text-muted-foreground" aria-hidden="true" />
            {voice?.name ?? t(isDesign ? 'projects.designed_voice' : 'projects.cloned_voice')}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Clock3Icon className="size-3" aria-hidden="true" />
              {formatRelative(item.created_at, i18n.language)}
            </span>
            {item.duration_seconds != null && item.generation_time != null ? (
              <span>
                {t('clone.output_meta', {
                  duration: formatSeconds(item.duration_seconds),
                  gen: formatSeconds(item.generation_time),
                })}
              </span>
            ) : null}
          </p>
        </div>
      </section>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">{t('clone.text_label')}</h3>
        <p className="whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
          {item.text}
        </p>
      </div>
      <dl className="space-y-2 text-sm">
        <div className="flex items-start justify-between gap-4">
          <dt className="shrink-0 text-muted-foreground">{t('clone.language')}</dt>
          <dd className="min-w-0 text-right" title={t('clone.take_language_used')}>
            <span className="block truncate">{takeLanguage}</span>
            {!sameLanguage(item.language, composerLanguage) && (
              <span
                data-slot="take-language-next"
                className="block truncate text-xs text-muted-foreground"
              >
                {t('clone.take_language_next', { language: languageLabel(composerLanguage) })}
              </span>
            )}
          </dd>
        </div>
        {item.seed != null ? (
          <div className="flex items-start justify-between gap-4">
            <dt className="text-muted-foreground">{t('clone.seed_label')}</dt>
            <dd className="text-right tabular-nums">{item.seed}</dd>
          </div>
        ) : null}
        {item.instruct && (
          <div className="flex items-start justify-between gap-4">
            <dt className="shrink-0 text-muted-foreground">{t('clone.style')}</dt>
            {/* One line that never runs under an overlay; the full style is the tooltip. */}
            <dd className="min-w-0 flex-1 truncate text-right" title={item.instruct}>
              {item.instruct}
            </dd>
          </div>
        )}
      </dl>
      {hasRecipe ? (
        <details className="group rounded-xl border border-border/60 bg-muted/15 p-3">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium">
            <SlidersHorizontalIcon className="size-4 text-muted-foreground" aria-hidden="true" />
            {t('clone.production_overrides')}
          </summary>
          <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2 border-t border-border/50 pt-3 text-xs sm:grid-cols-3">
            {numericSettings.map(([key, label]) => (
              <div key={key} className="min-w-0">
                <dt className="truncate text-muted-foreground">{t(label)}</dt>
                <dd className="mt-0.5 truncate font-medium tabular-nums">{settings[key]}</dd>
              </div>
            ))}
            {settings.duration !== undefined ? (
              <div className="min-w-0">
                <dt className="truncate text-muted-foreground">{t('clone.duration')}</dt>
                <dd className="mt-0.5 truncate font-medium tabular-nums">
                  {settings.duration || t('clone.auto')}
                </dd>
              </div>
            ) : null}
            {(['denoise', 'postprocess'] as const).map((key) =>
              settings[key] !== undefined ? (
                <div key={key} className="min-w-0">
                  <dt className="truncate text-muted-foreground">{t(`clone.${key}`)}</dt>
                  <dd className="mt-0.5 truncate font-medium">
                    {t(settings[key] ? 'common.yes' : 'common.no')}
                  </dd>
                </div>
              ) : null,
            )}
          </dl>
        </details>
      ) : null}
      {item.audio_path && (
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-sm font-medium">
            <AudioLinesIcon className="size-4 text-muted-foreground" aria-hidden="true" />
            {t('clone.output_title')}
          </p>
          <WaveformPlayer
            src={audioUrl(item.audio_path)}
            source={'take-' + item.id}
            durationHint={item.duration_seconds ?? undefined}
            height={64}
          />
        </div>
      )}
      {voice && (
        <LockVoice
          locked={Boolean(voice.is_locked)}
          key={item.id}
          profileId={voice.id}
          historyId={item.id}
          seed={item.seed}
          disabled={isGenerating}
        />
      )}
      {/* One row: the primary save, then the secondary actions as quiet icon buttons. */}
      <div data-slot="take-actions" className="flex flex-wrap items-center gap-1.5">
        <SaveTakeProfile key={item.id + '-save'} item={item} />
        {item.audio_path && (
          <SaveAudioButton
            url={audioUrl(item.audio_path)}
            mp3Url={audioMp3Url(item.audio_path)}
            suggestedName={`voicestudio-${item.id}.wav`}
            iconOnly
          />
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('clone.history_reuse')}
          title={t('clone.history_reuse')}
          disabled={isGenerating}
          onClick={() => void reuse()}
        >
          <RepeatIcon />
        </Button>
      </div>
    </div>
  );
}
