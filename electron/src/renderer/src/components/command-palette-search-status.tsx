import type { UseQueryResult } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

type SearchQuery = Pick<UseQueryResult, 'isError' | 'isFetching' | 'refetch'>;

export function CommandPaletteSearchStatus({
  hasResults,
  queries,
  projectQueries,
}: {
  hasResults: boolean;
  queries: readonly SearchQuery[];
  projectQueries: readonly SearchQuery[];
}) {
  const { t } = useTranslation();
  const searchQueries = [...queries, ...projectQueries];
  const failedQueries = searchQueries.filter((query) => query.isError);
  if (failedQueries.length > 0) {
    return (
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
        <p role="alert" className="text-destructive">
          {projectQueries.some((query) => query.isError) && (
            <span className="block">{t('projects.title')}</span>
          )}
          {t('common.error')}
        </p>
        <Button
          size="xs"
          variant="ghost"
          disabled={failedQueries.every((query) => query.isFetching)}
          onClick={() => {
            for (const query of failedQueries) void query.refetch({ cancelRefetch: false });
          }}
        >
          {t('common.retry')}
        </Button>
      </div>
    );
  }
  if (hasResults) return null;
  return (
    <p role="status" className="p-4 text-center text-sm text-muted-foreground">
      {searchQueries.some((query) => query.isFetching)
        ? t('common.loading')
        : t('preferences.no_matches')}
    </p>
  );
}
