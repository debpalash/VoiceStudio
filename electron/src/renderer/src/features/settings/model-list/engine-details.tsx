import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { BookOpenIcon } from 'lucide-react';
import { ExternalLink } from '@/components/external-link';

/** Setup help shown when an engine row is expanded. */
export function EngineDetails({
  detail,
  snippet,
  docsUrl,
  tools,
}: {
  detail?: string | null;
  snippet?: string | null;
  docsUrl?: string | null;
  tools?: ReactNode;
}) {
  const { t } = useTranslation();
  if (!detail && !snippet && !docsUrl && !tools) return null;
  return (
    <div className="space-y-2 py-1.5">
      {detail && (
        <p className="text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
          {detail}
        </p>
      )}
      {snippet && (
        <code className="block w-fit max-w-full overflow-x-auto rounded-md bg-muted/55 px-2 py-1 font-mono text-[11px] text-foreground/75">
          {snippet}
        </code>
      )}
      {(docsUrl || tools) && (
        <div className="flex flex-wrap items-center gap-2">
          {tools}
          {docsUrl && (
            <ExternalLink href={docsUrl}>
              <BookOpenIcon />
              {t('common.learn_more')}
            </ExternalLink>
          )}
        </div>
      )}
    </div>
  );
}

/** "2 of 7 installed" for engines that host several models. */
export function ModelCount({ installed, total }: { installed: number; total: number }) {
  const { t } = useTranslation();
  return (
    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
      {t('modelSettings.modelsInstalled', { installed, count: total })}
    </span>
  );
}
