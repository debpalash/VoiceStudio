import { runRendererTask } from '@/lib/global-error-recovery';
import { BlocksIcon, ChevronRightIcon, SearchIcon, SparklesIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from '@tanstack/react-router';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Input } from '@/components/ui/input';
import {
  INTEGRATION_CATALOG,
  integrationSlug,
} from '../../../../../../frontend/src/config/integration-catalog';
import { SPONSORS } from '../../../../../../frontend/src/config/sponsors';
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
    () => [
      ...SPONSORS.map((entry) => ({
        ...entry,
        featured: true,
        capabilities: [] as readonly string[],
        worksWith: false,
        category: null as string | null,
      })),
      ...INTEGRATION_CATALOG.map((entry) => {
        const setup = integrationSetup(integrationSlug(entry.name));
        return {
          ...entry,
          tier: '',
          featured: false,
          // Only entries with a real setup block claim capabilities; the rest
          // are external links to the provider.
          capabilities: (setup?.capabilities ?? []).map(
            (capability) => `integrationCatalog.capability.${capability}`,
          ),
          worksWith: Boolean(setup),
        };
      }),
    ],
    [],
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
          <header className="integrations-hero">
            <span className="integrations-hero-icon">
              <BlocksIcon aria-hidden="true" />
            </span>
            <div className="integrations-hero-copy">
              <div className="integrations-hero-title-row">
                <h2>{t('integrationCatalog.title')}</h2>
                <p>{t('integrationCatalog.description')}</p>
              </div>
              <p className="integrations-hero-notice">{t('directoryExamples.notice')}</p>
            </div>
          </header>

          {SPONSORS.length > 0 && (
            <section aria-labelledby="featured-integrations" className="integrations-featured">
              <div className="integrations-section-heading">
                <h3 id="featured-integrations">
                  <SparklesIcon aria-hidden="true" />
                  {t('integrationCatalog.featured')}
                </h3>
                <span>{SPONSORS.length}</span>
              </div>
              <div className="integrations-featured-grid">
                {SPONSORS.map((sponsor) => (
                  <button
                    type="button"
                    key={integrationSlug(sponsor.name)}
                    onClick={() => openIntegration(sponsor.name)}
                    className="integration-featured-card"
                  >
                    <img src={sponsor.logoUrl} alt="" loading="lazy" />
                    <strong>{sponsor.name}</strong>
                    <span>{t('integrationCatalog.featured')}</span>
                    <ChevronRightIcon aria-hidden="true" />
                  </button>
                ))}
              </div>
            </section>
          )}

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
              {INTEGRATION_CATEGORIES.map((id) => (
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
                key={`${entry.featured ? 'sponsor' : 'catalog'}:${integrationSlug(entry.name)}`}
                onClick={() => openIntegration(entry.name)}
                className="integration-card"
              >
                <div className="integration-card-top">
                  <img src={entry.logoUrl} alt="" loading="lazy" />
                  <ChevronRightIcon aria-hidden="true" />
                </div>
                <div className="integration-card-title">
                  <h3>{entry.name}</h3>
                  <span
                    className={
                      entry.featured
                        ? 'integration-badge integration-badge--featured'
                        : entry.worksWith
                          ? 'integration-badge integration-badge--works'
                          : 'integration-badge'
                    }
                  >
                    {t(
                      entry.featured
                        ? 'integrationCatalog.featured'
                        : entry.worksWith
                          ? 'integrationCatalog.worksWith'
                          : 'integrationCatalog.externalLink',
                    )}
                  </span>
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
