import { useContext, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRightIcon, SearchIcon, type LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeError } from '@/lib/api/client';
import { useModelCatalogue } from '../model-catalogue-query';
import { SettingsActionError } from '../settings-action-error';
import { SettingsRowsSkeleton } from '../settings-layout';
import { matchesQuery, normalizeQuery, sortByRank } from './linking';
import { FamilyRow, type FamilyItem } from './family-row';
import { ModelStoreShownContext } from './model-store-context';

/** Model rows and their download controls come from the catalogue; say when it failed. */
function CatalogueError() {
  const { t } = useTranslation();
  const catalogue = useModelCatalogue();
  if (!catalogue.isError) return null;
  return (
    <SettingsActionError
      className="m-4"
      title={t('modelSettings.failed')}
      detail={describeError(catalogue.error)}
      action={
        <Button size="xs" variant="ghost" onClick={() => void catalogue.refetch()}>
          {t('backend.retry')}
        </Button>
      }
    />
  );
}

/**
 * The single list of a Settings -> Models family: every engine and standalone
 * model, each with its install and select actions on the row.
 */
export function FamilyList({
  title,
  icon: Icon,
  items: allItems,
  header,
  error,
  footer,
}: {
  title: string;
  icon?: LucideIcon;
  /** Undefined while loading. */
  items?: FamilyItem[];
  header?: ReactNode;
  error?: ReactNode;
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const storeShown = useContext(ModelStoreShownContext);
  const items = storeShown ? allItems?.filter((item) => !item.installOnly) : allItems;
  const [query, setQuery] = useState('');
  const needle = normalizeQuery(query);
  const visible = items
    ? sortByRank(
        items
          .map((item) => ({
            item,
            childHit: Boolean(needle) && matchesQuery(item.childSearch ?? [], needle),
          }))
          .filter(({ item, childHit }) => childHit || matchesQuery(item.search, needle))
          .map(({ item, childHit }) => ({ ...item, childHit })),
      )
    : undefined;
  const main = visible?.filter((item) => !item.folded) ?? [];
  const folded = visible?.filter((item) => item.folded) ?? [];
  const searchable = (items?.length ?? 0) > 3;
  return (
    <section className="min-w-0 space-y-2.5">
      <div className="flex min-h-7 flex-wrap items-center justify-between gap-x-3 gap-y-2 px-1">
        <h2 className="flex items-center gap-2 text-sm font-normal tracking-[-0.005em] text-foreground/80">
          {Icon && <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />}
          {title}
          {items && (
            <span className="text-xs tabular-nums text-muted-foreground">{items.length}</span>
          )}
        </h2>
        {searchable && (
          <label className="flex h-8 w-full min-w-0 items-center gap-2 rounded-lg border border-border/60 bg-background/40 px-2.5 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30 @xl:w-64">
            <SearchIcon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            <Input
              type="search"
              aria-label={t('preferences.search')}
              placeholder={t('preferences.search')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setQuery('');
              }}
              className="h-7 min-w-0 border-0 bg-transparent px-0 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
            />
          </label>
        )}
      </div>
      <div className="glass-panel overflow-hidden rounded-xl border border-border/60 bg-card/40 shadow-xs/5 [&>*+*]:border-t [&>*+*]:border-border/50">
        <CatalogueError />
        {header}
        {error ??
          (!visible ? (
            <SettingsRowsSkeleton label={t('preferences.loading')} rows={3} />
          ) : (
            <>
              {main.map((item) => (
                <FamilyRow key={item.key} item={item} forceOpen={item.childHit} />
              ))}
              {folded.length > 0 && (
                <details className="group/folded">
                  <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
                    <ChevronRightIcon
                      aria-hidden="true"
                      className="size-4 transition-transform duration-150 group-open/folded:rotate-90 motion-reduce:transition-none"
                    />
                    {t('modelSettings.unavailable')} ({folded.length})
                  </summary>
                  <div className="border-t border-border/50 [&>*+*]:border-t [&>*+*]:border-border/50">
                    {folded.map((item) => (
                      <FamilyRow key={item.key} item={item} forceOpen={item.childHit} />
                    ))}
                  </div>
                </details>
              )}
              {visible.length === 0 && (
                <p role="status" className="px-4 py-6 text-center text-sm text-muted-foreground">
                  {needle ? t('preferences.no_matches') : t('modelSettings.empty')}
                </p>
              )}
            </>
          ))}
        {footer}
      </div>
    </section>
  );
}
