import {
  BabyIcon,
  BookOpenIcon,
  BriefcaseBusinessIcon,
  HourglassIcon,
  MapPinIcon,
  MegaphoneIcon,
  PartyPopperIcon,
  SmileIcon,
  SparklesIcon,
  TvIcon,
  WandSparklesIcon,
  WindIcon,
  ZapIcon,
  type LucideIcon,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { instructToVdStates } from '@shared/utils/voiceInstruct';

interface Personality {
  id: string;
  name: string;
  instruct: string;
  is_demo?: boolean;
}

const PERSONALITY_ICONS: Record<string, LucideIcon> = {
  narrator: BookOpenIcon,
  casual: SmileIcon,
  news_anchor: TvIcon,
  storyteller: WandSparklesIcon,
  corporate: BriefcaseBusinessIcon,
  energetic: ZapIcon,
};

/** Every starting point carries an icon, so the chip grid reads as one set. */
export const PRESET_ICONS: Record<string, LucideIcon> = {
  narrator: MegaphoneIcon,
  excited_child: BabyIcon,
  anxious_whisper: WindIcon,
  surprised_woman: PartyPopperIcon,
  elderly_story: HourglassIcon,
  sichuan: MapPinIcon,
};
export const presetIcon = (id: string) => PRESET_ICONS[id] ?? SparklesIcon;

/** One chip style for personalities and presets: a calm, evenly sized grid cell. */
export const STARTING_POINT_CHIP =
  'h-8 w-full min-w-0 justify-start gap-2 rounded-lg border border-border/35 bg-background/25 px-2.5 font-normal text-foreground/90 hover:border-border/60 hover:bg-accent/50 aria-pressed:border-primary/35 aria-pressed:bg-primary/10 aria-pressed:text-foreground [&>span]:truncate [&>svg]:text-muted-foreground aria-pressed:[&>svg]:text-primary';

export function sameRecipe(
  current: Record<string, string>,
  recipe: Record<string, string>,
): boolean {
  return Object.entries(recipe).every(([category, value]) => current[category] === value);
}

export function PersonalityPresets({
  disabled,
  attrs = {},
  onSelect,
}: {
  disabled: boolean;
  attrs?: Record<string, string>;
  onSelect: (attrs: Record<string, string>) => void;
}) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['personalities'],
    queryFn: ({ signal }) => apiJson<Personality[]>('/personalities', { signal }),
    staleTime: Infinity,
  });
  return (
    // Chips join the parent's grid so personalities and presets line up as one set.
    <div className="contents">
      {query.data
        ?.filter((personality) => !personality.is_demo)
        .map((personality) => {
          const recipe = instructToVdStates(personality.instruct);
          const active = sameRecipe(attrs, recipe);
          const Icon = PERSONALITY_ICONS[personality.id] || SparklesIcon;
          const label = t('clone.personality_' + personality.id, {
            defaultValue: personality.name,
          });
          return (
            <Button
              key={personality.id}
              type="button"
              size="sm"
              variant="ghost"
              className={STARTING_POINT_CHIP}
              title={label}
              aria-pressed={active}
              disabled={disabled}
              onClick={() => onSelect(active ? instructToVdStates('') : recipe)}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </Button>
          );
        })}
      {query.isError && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          disabled={disabled}
          onClick={() => void query.refetch()}
        >
          {t('backend.retry')}
        </Button>
      )}
    </div>
  );
}
