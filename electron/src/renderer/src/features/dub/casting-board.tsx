import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDownIcon, GripVerticalIcon, SparklesIcon, UserRoundIcon } from 'lucide-react';
import { toast } from 'sonner';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ProfileAvatar } from '@/components/profile-avatar';
import type { Profile } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { PRESETS } from '@shared/utils/constants';
import {
  autoProfileId,
  castParts,
  castSpeakers,
} from '@shared/utils/segments';
import type { DubSegment } from './dub-session';

const DEFAULT_VOICE = '__default__';

interface VoiceChoice {
  value: string;
  label: string;
  kind: 'default' | 'profile' | 'preset' | 'auto';
  profile?: Profile;
}

function VoiceIcon({ choice }: { choice: VoiceChoice }) {
  if (choice.profile)
    return (
      <ProfileAvatar
        name={choice.profile.name}
        imageUrl={choice.profile.image_url}
        className="size-5 text-[10px]"
      />
    );
  if (choice.kind === 'preset') return <SparklesIcon className="size-4 text-primary" />;
  return <UserRoundIcon className="size-4 text-muted-foreground" />;
}

export function CastingBoard({
  segments,
  profiles,
  disabled,
  onAssign,
}: {
  segments: DubSegment[];
  profiles: Profile[];
  disabled: boolean;
  onAssign: (speaker: string, profileId: string) => void;
}) {
  const { t } = useTranslation();
  // Automatic speaker matching is already a usable default. Keep the editor
  // folded until someone wants to override a voice, which makes the primary
  // upload → language → translate/generate path readable at a glance.
  const [open, setOpen] = useState(false);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const speakers = castSpeakers(segments) as string[];
  if (!speakers.length) return null;

  const currentVoice = (speaker: string) =>
    segments.find((segment) => segment.speaker_id === speaker)?.profile_id ||
    segments
      .flatMap(
        (segment) => castParts(segment) as Array<{ speaker_id?: string; profile_id?: string }>,
      )
      .find((part) => part.speaker_id === speaker)?.profile_id ||
    '';

  const baseChoices: VoiceChoice[] = [
    { value: DEFAULT_VOICE, label: t('dub.default'), kind: 'default' },
    ...profiles.map((profile) => ({
      value: profile.id,
      label: profile.name,
      kind: 'profile' as const,
      profile,
    })),
    ...PRESETS.map((preset) => ({
      value: `preset:${preset.id}`,
      label: t(`clone.preset_${preset.id}`),
      kind: 'preset' as const,
    })),
  ];

  const choicesFor = (speaker: string) => {
    const current = currentVoice(speaker);
    const automatic = autoProfileId(speaker);
    return current === automatic
      ? [
          {
            value: automatic,
            label: `${t('dub.auto')} · ${speaker}`,
            kind: 'auto' as const,
          },
          ...baseChoices,
        ]
      : baseChoices;
  };

  const assign = (speaker: string, raw: string) => {
    const value = raw === DEFAULT_VOICE ? '' : raw;
    const choice = choicesFor(speaker).find((item) => item.value === raw || item.value === value);
    if (!choice) return;
    onAssign(speaker, value);
    toast.success(t('dub.casting_assigned', { voice: choice.label, speaker }));
  };

  const chipChoices = baseChoices;

  return (
    <details
      open={open}
      className="group rounded-xl border border-border/60 bg-muted/20 p-3"
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
        if (!event.currentTarget.open) setDropTarget(null);
      }}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium">
        <span className="grid size-5 place-items-center rounded-full bg-primary/12 text-[11px] font-semibold text-primary">
          3
        </span>
        {t('dub.cast')}
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
          {speakers.length}
        </span>
        <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs leading-5 text-muted-foreground">{t('dub.casting_drag_hint')}</p>
        {chipChoices.length > 0 && (
          <div
            role="list"
            aria-label={t('dub.casting_voices')}
            className="flex flex-wrap gap-1.5 pb-1"
          >
            {chipChoices.map((choice) => (
              <span
                key={choice.value}
                role="listitem"
                draggable={!disabled}
                onDragStart={(event) => {
                  event.dataTransfer.setData('text/plain', choice.value);
                  event.dataTransfer.effectAllowed = 'copy';
                }}
                className="inline-flex h-8 max-w-36 shrink-0 cursor-grab items-center gap-1.5 rounded-lg border border-border/50 bg-background/45 px-2 text-xs shadow-xs active:cursor-grabbing"
                title={choice.label}
              >
                <GripVerticalIcon className="size-3 text-muted-foreground/70" />
                <VoiceIcon choice={choice} />
                <span className="truncate">{choice.label}</span>
              </span>
            ))}
          </div>
        )}
        <div className="space-y-2">
          {speakers.map((speaker) => {
            const choices = choicesFor(speaker);
            const current = currentVoice(speaker);
            const value = current || DEFAULT_VOICE;
            const selected = choices.find((choice) => choice.value === value) || choices[0];
            return (
              <div
                key={speaker}
                data-speaker={speaker}
                className={cn(
                  'rounded-lg border border-border/50 bg-background/25 p-2 transition-[background-color,border-color,box-shadow] duration-150',
                  dropTarget === speaker &&
                    'border-primary/60 bg-primary/8 shadow-[inset_0_0_0_1px_rgb(255_255_255/5%)]',
                )}
                onDragOver={(event) => {
                  if (disabled) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = 'copy';
                }}
                onDragEnter={() => !disabled && setDropTarget(speaker)}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                    setDropTarget((currentTarget) =>
                      currentTarget === speaker ? null : currentTarget,
                    );
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDropTarget(null);
                  if (!disabled) assign(speaker, event.dataTransfer.getData('text/plain'));
                }}
              >
                <div className="mb-2 flex min-w-0 items-center gap-2">
                  <UserRoundIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{speaker}</span>
                  <span className="max-w-32 truncate text-[10px] text-muted-foreground">
                    {selected.label}
                  </span>
                </div>
                <Select
                  items={choices}
                  value={value}
                  disabled={disabled}
                  onValueChange={(next) => typeof next === 'string' && assign(speaker, next)}
                >
                  <SelectTrigger
                    size="sm"
                    className="h-8 w-full bg-background/40"
                    aria-label={t('dub.casting_assign_to', { speaker })}
                  >
                    <SelectValue>
                      <VoiceIcon choice={selected} />
                      <span className="truncate">{selected.label}</span>
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent align="start">
                    <SelectItem value={DEFAULT_VOICE}>
                      <UserRoundIcon />
                      {t('dub.default')}
                    </SelectItem>
                    {profiles.length > 0 && (
                      <SelectGroup>
                        <SelectLabel>{t('dub.clone_profiles')}</SelectLabel>
                        {profiles.map((profile) => (
                          <SelectItem key={profile.id} value={profile.id}>
                            <ProfileAvatar
                              name={profile.name}
                              imageUrl={profile.image_url}
                              className="size-5 text-[10px]"
                            />
                            {profile.name}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    <SelectSeparator />
                    <SelectGroup>
                      <SelectLabel>{t('dub.design_presets')}</SelectLabel>
                      {PRESETS.map((preset) => (
                        <SelectItem key={preset.id} value={`preset:${preset.id}`}>
                          <SparklesIcon />
                          {t(`clone.preset_${preset.id}`)}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>
            );
          })}
        </div>
      </div>
    </details>
  );
}
