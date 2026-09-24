import { useMemo, useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { integrationSetup, type IntegrationSetup, type SetupBlock } from './setup-registry';
import { integrationCategoryKey } from './integration-categories';
import { IntegrationDetailColumns } from './integration-detail-layout';
import { saveLocalFile } from '@/lib/local-export';
import { describeError } from '@/lib/api/client';
import {
  ArrowLeftIcon,
  BlocksIcon,
  BookOpenIcon,
  CircleCheckIcon,
  ExternalLinkIcon,
} from 'lucide-react';
import { Link, useParams } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { getBridge } from '@/components/bridge';
import {
  getIntegrationBySlug,
  integrationSlug,
  type IntegrationCatalogEntry,
} from '../../../../../../frontend/src/config/integration-catalog';
import { SPONSORS } from '../../../../../../frontend/src/config/sponsors';
import './integrations-page.css';

/** Catalog entries and featured sponsors both have an in-app page. */
function findIntegration(slug: string): IntegrationCatalogEntry | undefined {
  const entry = getIntegrationBySlug(slug);
  if (entry) return entry;
  const sponsor = SPONSORS.find((item) => integrationSlug(item.name) === slug);
  return sponsor && { ...sponsor, category: '', featured: true };
}

function openExternal(url: string) {
  const bridge = getBridge();
  if (bridge) void bridge.files.openExternal(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
}

function SetupBlockView({ block }: { block: SetupBlock }) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const headingId = `setup-${block.id}`;
  return (
    <section className="integration-setup-block space-y-2" aria-labelledby={headingId}>
      <h4 id={headingId}>{t(block.titleKey)}</h4>
      {block.hintKey && <p>{t(block.hintKey, block.hintValues)}</p>}
      <pre
        className="max-h-80 overflow-auto rounded-lg bg-muted/40 p-4 text-xs"
        data-language={block.language}
      >
        <code>{block.text}</code>
      </pre>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(block.text);
              toast.success(t('transcriptions.copied'));
            } catch {
              toast.error(t('transcriptions.copy_failed'));
            }
          }}
        >
          {t('transcriptions.copy')}
        </Button>
        {block.download && (
          <Button
            variant="outline"
            disabled={saving}
            aria-busy={saving}
            onClick={async () => {
              setSaving(true);
              try {
                const result = await saveLocalFile(
                  new Blob([block.text], { type: block.download!.type }),
                  block.download!.file,
                );
                if (!result.canceled) toast.success(t('nav.saved'));
              } catch (error) {
                toast.error(t('clone.download_failed', { message: describeError(error) }));
              } finally {
                setSaving(false);
              }
            }}
          >
            {t('clone.download')}
          </Button>
        )}
      </div>
    </section>
  );
}

function DetailHero({
  entry,
  setup,
  status,
  action,
}: {
  entry: IntegrationCatalogEntry;
  setup: IntegrationSetup | undefined;
  status?: ReactNode;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="integration-detail-hero" aria-labelledby="integration-detail-name">
      <div className="integration-detail-logo">
        <img src={entry.logoUrl} alt="" />
      </div>
      <div className="integration-detail-hero-copy">
        <p className="integration-detail-kicker">
          <BlocksIcon aria-hidden="true" />
          {t(integrationCategoryKey(entry.category))}
        </p>
        <div className="integration-detail-title-row">
          <h2 id="integration-detail-name">{entry.name}</h2>
          {status}
        </div>
        <p className="integration-detail-tagline">
          {t(setup ? setup.taglineKey : 'integrationCatalog.externalHint')}
        </p>
      </div>
      {(action || setup) && (
        <div className="integration-detail-hero-actions">
          {action}
          {setup && (
            // The page's only documentation link.
            <a
              href={setup.docs}
              target="_blank"
              rel="noopener noreferrer"
              className="integration-docs-link"
            >
              <BookOpenIcon aria-hidden="true" />
              {t('common.learn_more')}
            </a>
          )}
        </div>
      )}
    </section>
  );
}

export function IntegrationDetailPage() {
  const { t } = useTranslation();
  const { slug } = useParams({ strict: false });
  const backend = useBackendStatus();
  const setup = integrationSetup(slug ?? '');
  const blocks = useMemo(() => setup?.blocks(backend.baseUrl) ?? null, [setup, backend.baseUrl]);
  const entry = findIntegration(slug ?? '');
  if (!entry) {
    return (
      <div className="integrations-page">
        <WorkspaceHeader>
          <h1 className="text-sm font-medium">{t('integrationCatalog.title')}</h1>
        </WorkspaceHeader>
        <main className="integrations-content">
          <div className="integrations-container integrations-detail-empty">
            <p>{t('common.no_matches')}</p>
            <Link to="/integrations" className="integration-back-link">
              <ArrowLeftIcon />
              {t('common.back')}
            </Link>
          </div>
        </main>
      </div>
    );
  }
  const capabilities = (
    <section className="integration-detail-panel" aria-labelledby="integration-capabilities">
      <h3 id="integration-capabilities">{t('integrationCatalog.capabilitiesTitle')}</h3>
      {setup ? (
        <>
          <p className="integration-works-with">
            <CircleCheckIcon aria-hidden="true" />
            {t('integrationCatalog.worksWith')}
          </p>
          <div className="integration-capabilities">
            {setup.capabilities.map((capability) => (
              <span key={capability}>{t(`integrationCatalog.capability.${capability}`)}</span>
            ))}
          </div>
        </>
      ) : (
        <p className="integration-detail-note">{t('directoryExamples.notice')}</p>
      )}
    </section>
  );
  const website = (
    <section
      className="integration-detail-panel integration-detail-action"
      aria-labelledby="integration-website"
    >
      <h3 id="integration-website">{t('integrationCatalog.websiteTitle')}</h3>
      <p className="integration-detail-url">{entry.url}</p>
      <button
        type="button"
        onClick={() => openExternal(entry.url)}
        className="integration-open-button"
      >
        {t('common.open')}
        <ExternalLinkIcon aria-hidden="true" />
      </button>
    </section>
  );
  const rail = (
    <>
      {capabilities}
      {website}
    </>
  );
  const hero = (slots: { status?: ReactNode; action?: ReactNode }) => (
    <DetailHero entry={entry} setup={setup} {...slots} />
  );

  let body: ReactNode;
  if (setup?.panel) {
    body = <setup.panel hero={hero} rail={rail} />;
  } else if (setup) {
    body = (
      <>
        {hero({})}
        <IntegrationDetailColumns
          main={
            <section
              className="integration-detail-panel space-y-4"
              aria-labelledby="integration-setup-title"
            >
              <h3 id="integration-setup-title">
                {t('integrationCatalog.setupTitle', { name: entry.name })}
              </h3>
              {blocks ? (
                blocks.map((block) => <SetupBlockView key={block.id} block={block} />)
              ) : (
                <p>{t('integrationCatalog.setupUnavailable')}</p>
              )}
              {setup.voiceBindings && (
                <Link to="/settings/sharing" className="integration-inline-link">
                  {t('integrationCatalog.voiceBindings')}
                </Link>
              )}
            </section>
          }
          railBottom={rail}
        />
      </>
    );
  } else {
    body = (
      <>
        {hero({})}
        <div className="integration-detail-grid">{rail}</div>
      </>
    );
  }

  return (
    <div className="integrations-page">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{entry.name}</h1>
      </WorkspaceHeader>
      <main className="integrations-content">
        <div className="integrations-container integrations-detail">
          <Link to="/integrations" className="integration-back-link">
            <ArrowLeftIcon aria-hidden="true" />
            {t('common.back')}
          </Link>
          {body}
        </div>
      </main>
    </div>
  );
}
