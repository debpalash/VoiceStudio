import { runRendererTask } from '@/lib/global-error-recovery';
import { ChevronRightIcon, SearchIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from '@tanstack/react-router';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Input } from '@/components/ui/input';
import { INTEGRATION_CATALOG, integrationSlug } from '@shared/config/integration-catalog';
import { integrationSetup } from './setup-registry';
import { INTEGRATION_CATEGORIES, integrationCategoryKey } from './integration-categories';
import './integrations-page.css';

export function IntegrationsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  // Every logo opens its in-app page; vendor websites are only on its Website card.
  const openIntegration = (name: string) =>
    runRendererTask('Open integration', () =>
      navigate({ to: '/integrations/$slug', params: { slug: integrationSlug(name) } }),
    );
  const entries = useMemo(
    () =>
      INTEGRATION_CATALOG.flatMap((entry) => {
        const setup = integrationSetup(integrationSlug(entry.name));
        if (!setup) return [];
        return [
          {
            ...entry,
            capabilities: setup.capabilities.map(
              (capability) => `integrationCatalog.capability.${capability}`,
            ),
          },
        ];
      }),
    [],
  );
  const categories = useMemo(
    () => INTEGRATION_CATEGORIES.filter((id) => entries.some((entry) => entry.category === id)),
    [entries],
  );
  const filtered = entries.filter((entry) => {
    const haystack =
      `${entry.name} ${entry.url} ${entry.capabilities.map((key) => t(key)).join(' ')} ${entry.category ? t(integrationCategoryKey(entry.category)) : ''}`.toLocaleLowerCase();
    return (
      haystack.includes(query.trim().toLocaleLowerCase()) &&
      (!category || entry.category === category)
    );
  });
  return (
    <div className="integrations-page">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('integrationCatalog.title')}</h1>
      </WorkspaceHeader>
      <main className="integrations-content">
        <div className="integrations-container">
          <div className="integrations-toolbar">
            <label className="integrations-search">
              <SearchIcon aria-hidden="true" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t('common.search')}
                aria-label={t('common.search')}
              />
            </label>
            <div
              className="integrations-filters"
              role="group"
              aria-label={t('integrationCatalog.categoryFilter')}
            >
              <button
                type="button"
                aria-pressed={category === null}
                onClick={() => setCategory(null)}
              >
                {t('integrationCatalog.category.all')}
              </button>
              {categories.map((id) => (
                <button
                  type="button"
                  key={id}
                  aria-pressed={category === id}
                  onClick={() => setCategory(category === id ? null : id)}
                >
                  {t(integrationCategoryKey(id))}
                </button>
              ))}
            </div>
          </div>

          <section aria-live="polite" className="integrations-grid">
            {filtered.map((entry) => (
              <button
                type="button"
                key={integrationSlug(entry.name)}
                onClick={() => openIntegration(entry.name)}
                className="integration-card"
              >
                <div className="integration-card-top">
                  <img src={entry.logoUrl} alt="" loading="lazy" />
                  <ChevronRightIcon aria-hidden="true" />
                </div>
                <div className="integration-card-title">
                  <h3>{entry.name}</h3>
                  <span className="integration-badge integration-badge--works">
                    {t('integrationCatalog.worksWith')}
                  </span>
                  {entry.featured && (
                    <span className="integration-badge integration-badge--featured">
                      {t('integrationCatalog.featured')}
                    </span>
                  )}
                </div>
                {entry.capabilities.length > 0 && (
                  <p className="integration-card-capabilities">
                    {entry.capabilities.map((key) => t(key)).join(' · ')}
                  </p>
                )}
                <span className="integration-card-url">{entry.url}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="integrations-empty">{t('common.no_matches')}</p>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
