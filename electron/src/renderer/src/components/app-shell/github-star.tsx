import { useTranslation } from 'react-i18next';
import { StarIcon } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { getBridge } from '@/components/bridge';
import { runRendererTask } from '@/lib/global-error-recovery';
import { REPO_URL } from '@shared/utils/contactLinks';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';

function GithubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M12 .5a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.23c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.33-1.76-1.33-1.76-1.09-.75.08-.73.08-.73 1.2.08 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.49.99.11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.34-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.6-2.81 5.63-5.49 5.93.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12 12 0 0 0 12 .5Z" />
    </svg>
  );
}

const STARS_URL = 'https://api.github.com/repos/debpalash/VoiceStudio';
const REFRESH_MS = 20 * 60 * 1000;
// The last bundled count remains visible when GitHub cannot be reached.
const BUNDLED_STARS = 43_638;
const formatCount = new Intl.NumberFormat('en');

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
            className="github-star-shortcut app-no-drag inline-flex h-8 shrink-0 items-center gap-1.5 px-2.5 text-xs font-semibold focus-visible:outline-2 focus-visible:outline-ring"
            onClick={(event) => {
              const bridge = getBridge();
              if (!bridge) return;
              event.preventDefault();
              runRendererTask('Open GitHub', () => bridge.files.openExternal(REPO_URL));
            }}
          />
        }
      >
        <GithubIcon className="size-3.5" />
        <span>{t('support.star_short')}</span>
        <span className="github-star-count">
          <StarIcon aria-hidden="true" className="size-3" />
          {formatCount.format(stars.data ?? BUNDLED_STARS)}
        </span>
      </TooltipTrigger>
      <TooltipContent surface="theme" side="bottom">
        {t('support.star_github')}
      </TooltipContent>
    </Tooltip>
  );
}
