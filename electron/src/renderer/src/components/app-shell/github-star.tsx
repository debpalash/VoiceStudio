import { useTranslation } from 'react-i18next';
import { StarIcon } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { getBridge } from '@/components/bridge';
import { runRendererTask } from '@/lib/global-error-recovery';
import { REPO_URL } from '@shared/utils/contactLinks';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';

const STARS_URL = 'https://api.github.com/repos/debpalash/VoiceStudio';
const REFRESH_MS = 20 * 60 * 1000;
// The last bundled count remains visible when GitHub cannot be reached.
const BUNDLED_STARS = 43_638;
const formatCount = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const formatExact = new Intl.NumberFormat('en');

async function fetchStarCount(signal: AbortSignal): Promise<number> {
  const response = await fetch(STARS_URL, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
  });
  if (!response.ok) throw new Error('GitHub star count unavailable');
  const data: unknown = await response.json();
  const count = (data as { stargazers_count?: unknown } | null)?.stargazers_count;
  if (!Number.isSafeInteger(count) || (count as number) < 0)
    throw new Error('Invalid GitHub star count');
  return count as number;
}

export function GithubStar() {
  const { t } = useTranslation();
  const stars = useQuery({
    // Local combined-PR previews can suppress unsolicited external requests.
    enabled: import.meta.env.VITE_PREVIEW_OFFLINE !== '1',
    queryKey: ['github-star-count'],
    queryFn: ({ signal }) => fetchStarCount(signal),
    staleTime: REFRESH_MS,
    gcTime: REFRESH_MS,
    refetchInterval: REFRESH_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={t('support.star_github')}
            className="github-star-shortcut app-no-drag inline-flex shrink-0 items-stretch text-xs font-medium focus-visible:outline-2 focus-visible:outline-ring"
            onClick={(event) => {
              const bridge = getBridge();
              if (!bridge) return;
              event.preventDefault();
              runRendererTask('Open GitHub', () => bridge.files.openExternal(REPO_URL));
            }}
          />
        }
      >
        <span className="github-star-label">
          <StarIcon aria-hidden="true" className="size-3.5" />
          <span>{t('support.star_short')}</span>
        </span>
        <span className="github-star-count">{formatCount.format(stars.data ?? BUNDLED_STARS)}</span>
      </TooltipTrigger>
      <TooltipContent surface="theme" side="bottom">
        {t('support.star_github')} · {formatExact.format(stars.data ?? BUNDLED_STARS)}
      </TooltipContent>
    </Tooltip>
  );
}
