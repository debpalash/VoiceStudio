import {
  BookOpenIcon,
  BriefcaseBusinessIcon,
  SmileIcon,
  SparklesIcon,
  TvIcon,
  WandSparklesIcon,
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

function sameRecipe(current: Record<string, string>, recipe: Record<string, string>): boolean {
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
    <div className="flex flex-wrap gap-1">
      {query.data
        ?.filter((personality) => !personality.is_demo)
        .map((personality) => {
          const recipe = instructToVdStates(personality.instruct);
          const active = sameRecipe(attrs, recipe);
          const Icon = PERSONALITY_ICONS[personality.id] || SparklesIcon;
          return (
            <Button
              key={personality.id}
              type="button"
              size="xs"
              variant={active ? 'secondary' : 'ghost'}
              aria-pressed={active}
              disabled={disabled}
              onClick={() => onSelect(active ? instructToVdStates('') : recipe)}
            >
              <Icon aria-hidden="true" />
              {t('clone.personality_' + personality.id, {
                defaultValue: personality.name,
              })}
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
