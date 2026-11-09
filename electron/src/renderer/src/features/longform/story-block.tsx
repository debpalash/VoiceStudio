import { type ComponentType, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AnnoyedIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  CircleCheckIcon,
  CircleQuestionMarkIcon,
  FingerprintIcon,
  LaughIcon,
  PauseIcon,
  PlayIcon,
  SlidersHorizontalIcon,
  TrashIcon,
  UserIcon,
  WindIcon,
  ZapIcon,
} from 'lucide-react';
import { isChapterLine } from '@shared/utils/storyExport';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { WaveformPlayer } from '@/components/waveform-player';
import { cn } from '@/lib/utils';
import type { Draft } from './longform-session';
import { AutoSizeTextarea, ChipPopover, ChoiceList } from './story-chips';

type Line = Draft['lines'][number];
type Profile = { id: string; name: string };

const STORY_TONES = [
  { tag: '[laughter]', icon: LaughIcon, key: 'laugh' },
  { tag: '[sigh]', icon: WindIcon, key: 'sigh' },
  { tag: '[question-en]', icon: CircleQuestionMarkIcon, key: 'question' },
  { tag: '[surprise-wa]', icon: ZapIcon, key: 'surprise' },
  { tag: '[confirmation-en]', icon: CircleCheckIcon, key: 'confirm' },
  { tag: '[dissatisfaction-hnn]', icon: AnnoyedIcon, key: 'dissatisfaction' },
] as const;

/** A line keeps its own speed only when it is a positive number (see linesWithOwnSpeed). */
export const hasOwnSpeed = (line: Line) => typeof line.speed === 'number' && line.speed > 0;

function PopoverHeading({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 px-1 pt-0.5 text-xs font-medium text-muted-foreground">
      <span>{children}</span>
      {aside}
    </div>
  );
}

function BlockAction({
  label,
  icon: Icon,
  disabled,
  onClick,
}: {
  label: string;
  icon: ComponentType;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={label}
            disabled={disabled}
            onClick={onClick}
          />
        }
      >
        <Icon />
      </TooltipTrigger>
      <TooltipContent surface="theme" side="top">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * One script line. Text first; who speaks it, which voice and any tone/speed
 * override sit in a row of chips underneath, and the line actions appear as a
 * small toolbar on hover or keyboard focus. A `# Title` line is a chapter
 * marker, so it renders as a compact heading without the voice controls.
 */
export function StoryBlock({
  line,
  index,
  count,
  draft,
  profiles,
  disabled,
  canPreview,
  previewing,
  previewUrl,
  inputRef,
  onUpdate,
  onPreview,
  onStopPreview,
  onInsert,
  onInlineVoice,
  onMove,
  onRemove,
}: {
  line: Line;
  index: number;
  count: number;
  draft: Draft;
  profiles: Profile[];
  disabled: boolean;
  canPreview: boolean;
  previewing: boolean;
  previewUrl: string | null;
  inputRef: (node: HTMLTextAreaElement | null) => void;
  onUpdate: (patch: Partial<Line>) => void;
  onPreview: () => void;
  onStopPreview: () => void;
  onInsert: (token: string) => void;
  onInlineVoice: (profileId: string) => void;
  onMove: (direction: -1 | 1) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const heading = isChapterLine(line.text);
  const character = draft.cast.find((c) => c.id === line.character);
  // Same lookup order as storyVoicesReady: a removed profile reads as
  // unavailable instead of falling through to a voice that will not be used.
  const voiceId = line.profileId || character?.profileId;
  const voice = voiceId ? profiles.find((p) => p.id === voiceId) : undefined;
  const voiceName = voiceId
    ? (voice?.name ?? t('modelSettings.unavailable'))
    : t('stories.defaultVoice');
  const speakerName = character?.name || t('stories.speakerNone');
  const ownSpeed = hasOwnSpeed(line);
  const speed = line.speed ?? draft.globalSpeed;
  const speedText = `${speed.toFixed(2)}×`;
  return (
    <div
      data-slot="story-block"
      data-kind={heading ? 'heading' : 'line'}
      className={cn(
        'group/block relative rounded-lg border border-transparent px-3 transition-colors focus-within:border-border/60 focus-within:bg-muted/15 hover:border-border/60 hover:bg-muted/15',
        heading ? 'py-1.5' : 'py-2',
        heading && index > 0 && 'mt-4',
      )}
    >
      <AutoSizeTextarea
        inputRef={inputRef}
        aria-label={t(heading ? 'stories.chapterHeading' : 'stories.linePlaceholder')}
        placeholder={heading ? undefined : t('stories.linePlaceholder')}
        className={cn(
          'pe-2',
          heading ? 'text-base font-semibold leading-7 tracking-tight' : 'text-sm leading-6',
        )}
        value={line.text}
        disabled={disabled}
        onChange={(e) => onUpdate({ text: e.target.value })}
      />
      {!heading && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <ChipPopover
            label={t('stories.speakerChip', { name: speakerName })}
            icon={UserIcon}
            tone={character ? 'default' : 'muted'}
            disabled={disabled}
            content={(close) => (
              <>
                <PopoverHeading>{t('stories.speaker')}</PopoverHeading>
                <ChoiceList
                  label={t('stories.speaker')}
                  options={[{ id: '', name: t('stories.speakerNone') }, ...draft.cast]}
                  value={character ? character.id : ''}
                  disabled={disabled}
                  onSelect={(id) => {
                    onUpdate({ character: id || undefined });
                    close();
                  }}
                />
              </>
            )}
          >
            {speakerName}
          </ChipPopover>
          <ChipPopover
            label={t('stories.voiceChip', { name: voiceName })}
            icon={FingerprintIcon}
            tone={line.profileId ? 'default' : 'muted'}
            disabled={disabled}
            contentClassName="w-72"
            content={(close) => (
              <>
                <PopoverHeading>{t('stories.lineVoice')}</PopoverHeading>
                <ChoiceList
                  label={t('stories.lineVoice')}
                  options={[{ id: null, name: t('stories.defaultVoice') }, ...profiles]}
                  value={line.profileId}
                  disabled={disabled}
                  onSelect={(profileId) => {
                    onUpdate({ profileId });
                    close();
                  }}
                />
                {profiles.length > 0 && (
                  <div className="space-y-1.5 border-t border-border/50 pt-2">
                    <PopoverHeading>{t('stories.inlineVoice')}</PopoverHeading>
                    <p className="px-1 text-xs text-muted-foreground">
                      {t('stories.inlineVoiceHint')}
                    </p>
                    <div className="flex flex-wrap gap-1 px-1">
                      {profiles.map((profile) => (
                        <Button
                          key={profile.id}
                          size="xs"
                          variant="outline"
                          disabled={disabled}
                          onClick={() => {
                            onInlineVoice(profile.id);
                            close();
                          }}
                        >
                          {profile.name}
                        </Button>
                      ))}
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={disabled}
                        onClick={() => {
                          onInlineVoice('default');
                          close();
                        }}
                      >
                        {t('stories.resetInlineVoice')}
                      </Button>
                    </div>
                  </div>
                )}
              </>
            )}
          >
            {voiceName}
          </ChipPopover>
          <ChipPopover
            label={t('stories.toneChip', { speed: speedText })}
            icon={SlidersHorizontalIcon}
            tone={ownSpeed ? 'accent' : 'muted'}
            disabled={disabled}
            contentClassName="w-72"
            content={(close) => (
              <>
                <PopoverHeading>{t('stories.tune')}</PopoverHeading>
                <div className="flex flex-wrap gap-1 px-1">
                  {STORY_TONES.map((tone) => {
                    const Icon = tone.icon;
                    return (
                      <Button
                        key={tone.tag}
                        size="xs"
                        variant="outline"
                        disabled={disabled}
                        title={tone.tag}
                        onClick={() => {
                          onInsert(tone.tag);
                          close();
                        }}
                      >
                        <Icon />
                        {t(`stories.tones.${tone.key}`)}
                      </Button>
                    );
                  })}
                </div>
                <div className="space-y-1 border-t border-border/50 px-1 pt-2">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="font-medium">{t('stories.speed')}</span>
                    <span className="tabular-nums text-foreground/90">{speedText}</span>
                  </div>
                  <input
                    className="w-full accent-primary"
                    aria-label={t('stories.speed')}
                    type="range"
                    min="0.5"
                    max="2"
                    step="0.05"
                    value={speed}
                    disabled={disabled}
                    onChange={(e) => onUpdate({ speed: Number(e.target.value) })}
                  />
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={disabled || !ownSpeed}
                    onClick={() => onUpdate({ speed: null })}
                  >
                    {t('stories.reset')}
                  </Button>
                </div>
              </>
            )}
          >
            {ownSpeed ? speedText : undefined}
          </ChipPopover>
        </div>
      )}
      {previewing && (
        <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
          <span role="status">{t('common.loading')}</span>
          <Button size="xs" variant="ghost" onClick={onStopPreview}>
            {t('common.stop')}
          </Button>
        </div>
      )}
      {previewUrl && (
        <div className="mt-2">
          <WaveformPlayer showWaveform={false} src={previewUrl} source="story-line-preview" />
        </div>
      )}
      <div
        role="toolbar"
        aria-label={t('stories.lineActions')}
        className="absolute -top-3 end-2 z-10 flex items-center gap-0.5 rounded-lg border border-border/60 bg-background/95 p-0.5 opacity-0 shadow-sm transition-opacity group-focus-within/block:opacity-100 group-hover/block:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100"
      >
        {!heading && (
          <>
            <BlockAction
              label={t('stories.preview')}
              icon={PlayIcon}
              disabled={disabled || !canPreview}
              onClick={onPreview}
            />
            <BlockAction
              label={t('stories.insertPause')}
              icon={PauseIcon}
              disabled={disabled}
              onClick={() => onInsert('[pause 0.5s]')}
            />
          </>
        )}
        <BlockAction
          label={t('stories.moveUp')}
          icon={ArrowUpIcon}
          disabled={disabled || index === 0}
          onClick={() => onMove(-1)}
        />
        <BlockAction
          label={t('stories.moveDown')}
          icon={ArrowDownIcon}
          disabled={disabled || index === count - 1}
          onClick={() => onMove(1)}
        />
        <BlockAction
          label={t('stories.removeLine')}
          icon={TrashIcon}
          disabled={disabled}
          onClick={onRemove}
        />
      </div>
    </div>
  );
}
