import { runRendererTask } from '@/lib/global-error-recovery';
import { BlocksIcon, ExternalLinkIcon, SearchIcon, SparklesIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from '@tanstack/react-router';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { getBridge } from '@/components/bridge';
import { Input } from '@/components/ui/input';
import {
  INTEGRATION_CATALOG,
  integrationSlug,
} from '../../../../../../frontend/src/config/integration-catalog';
import { SPONSORS } from '../../../../../../frontend/src/config/sponsors';
import './integrations-page.css';

const categories = [
  ['comms', 'nav.dub', 'Calling & voice agents'],
  ['automation', 'tools.title', 'Automation'],
  ['agents', 'dub.choose_translation_agent', 'Agents'],
  ['mcp', 'settings.mcp_title', 'MCP'],
  ['developer', 'tools.title', 'Developer tools'],
  ['data', 'engineSidebar.asr', 'AI and data'],
  ['productivity', 'tools.title', 'Productivity'],
] as const;

function openExternal(url: string) {
  const bridge = getBridge();
  if (bridge) void bridge.files.openExternal(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
}

export function IntegrationsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  const entries = useMemo(
    () => [
      ...SPONSORS.map((entry) => ({
        ...entry,
        featured: true,
        directory: false,
        detailKeys: [] as string[],
        category: null as string | null,
      })),
      ...INTEGRATION_CATALOG.map((entry) => ({
        ...entry,
        tier: '',
        featured: false,
        directory: true,
      })),
    ],
    [],
  );
  const filtered = entries.filter((entry) => {
    const haystack =
      `${entry.name} ${entry.url} ${entry.detailKeys.join(' ')} ${entry.category ?? ''}`.toLocaleLowerCase();
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
                  onClick={() => openExternal(sponsor.url)}
                  className="integration-featured-card"
                >
                  <img src={sponsor.logoUrl} alt="" loading="lazy" />
                  <strong>{sponsor.name}</strong>
                  <span>{t('integrationCatalog.featured')}</span>
                  <ExternalLinkIcon aria-hidden="true" />
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
            aria-label={t('integrationCatalog.title')}
          >
            <button
              type="button"
              aria-pressed={category === null}
              onClick={() => setCategory(null)}
            >
              {t('common.clear')}
            </button>
            {categories.map(([id, key, fallback]) => (
              <button
                type="button"
                key={id}
                aria-pressed={category === id}
                onClick={() => setCategory(category === id ? null : id)}
              >
                {t(key, { defaultValue: fallback })}
              </button>
            ))}
          </div>
        </div>

        <section aria-live="polite" className="integrations-grid">
          {filtered.map((entry) => (
            <button
              type="button"
              key={`${entry.featured ? 'sponsor' : 'catalog'}:${integrationSlug(entry.name)}`}
              onClick={() =>
                runRendererTask('Open integration', () =>
                  navigate({
                    to: '/integrations/$slug',
                    params: { slug: integrationSlug(entry.name) },
                  }),
                )
              }
              className="integration-card"
            >
              <div className="integration-card-top">
                <img src={entry.logoUrl} alt="" loading="lazy" />
                <ExternalLinkIcon aria-hidden="true" />
              </div>
              <div className="integration-card-title">
                <h3>{entry.name}</h3>
                <span
                  className={
                    entry.featured
                      ? 'integration-badge integration-badge--featured'
                      : 'integration-badge'
                  }
                >
                  {t(entry.featured ? 'integrationCatalog.featured' : 'directoryExamples.example')}
                </span>
              </div>
              <p>
                {entry.directory && entry.detailKeys.length
                  ? entry.detailKeys.map((key) => t(key)).join(' · ')
                  : t('integrationCatalog.description')}
              </p>
              <span className="integration-card-url">{entry.url}</span>
            </button>
          ))}
          {filtered.length === 0 && <p className="integrations-empty">{t('common.no_matches')}</p>}
        </section>
      </main>
    </div>
  );
}
