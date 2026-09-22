import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { integrationSetup, type SetupBlock } from './setup-registry';
import { saveLocalFile } from '@/lib/local-export';
import { describeError } from '@/lib/api/client';
import { ArrowLeftIcon, ExternalLinkIcon, BlocksIcon, CircleCheckIcon } from 'lucide-react';
import { Link, useParams } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { getBridge } from '@/components/bridge';
import { getIntegrationBySlug } from '../../../../../../frontend/src/config/integration-catalog';
import './integrations-page.css';

const categoryLabels: Record<string, [string, string]> = {
  comms: ['nav.dub', 'Calling & voice agents'],
  automation: ['tools.title', 'Automation'],
  agents: ['dub.choose_translation_agent', 'Agents'],
  mcp: ['settings.mcp_title', 'MCP'],
  developer: ['tools.title', 'Developer tools'],
  data: ['engineSidebar.asr', 'AI and data'],
  productivity: ['tools.title', 'Productivity'],
};

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

export function IntegrationDetailPage() {
  const { t } = useTranslation();
  const { slug } = useParams({ strict: false });
  const backend = useBackendStatus();
  const setup = integrationSetup(slug ?? '');
  const blocks = useMemo(() => setup?.blocks(backend.baseUrl) ?? null, [setup, backend.baseUrl]);
  const entry = getIntegrationBySlug(slug ?? '');
  if (!entry) {
    return (
      <div className="integrations-page">
        <WorkspaceHeader>
          <h1 className="text-sm font-medium">{t('integrationCatalog.title')}</h1>
        </WorkspaceHeader>
        <main className="integrations-content integrations-detail-empty">
          <p>{t('common.no_matches')}</p>
          <Link to="/integrations" className="integration-back-link">
            <ArrowLeftIcon />
            {t('common.back')}
          </Link>
        </main>
      </div>
    );
  }
  const [categoryKey, categoryFallback] = categoryLabels[entry.category] ?? [
    'tools.title',
    'Integration',
  ];
  const openExternal = () => {
    const bridge = getBridge();
    if (bridge) void bridge.files.openExternal(entry.url);
    else window.open(entry.url, '_blank', 'noopener,noreferrer');
  };
  return (
    <div className="integrations-page">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{entry.name}</h1>
      </WorkspaceHeader>
      <main className="integrations-content integrations-detail">
        <Link to="/integrations" className="integration-back-link">
          <ArrowLeftIcon />
          {t('common.back')}
        </Link>
        <section className="integration-detail-hero">
          <div className="integration-detail-logo">
            <img src={entry.logoUrl} alt="" />
          </div>
          <div>
            <p className="integration-detail-kicker">
              <BlocksIcon />
              {t(categoryKey, { defaultValue: categoryFallback })}
            </p>
            <h2>{entry.name}</h2>
            <p>
              {t(setup ? 'integrationCatalog.worksWithHint' : 'integrationCatalog.externalHint')}
            </p>
          </div>
        </section>
        {setup && (
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
            <div className="flex flex-wrap items-center gap-3">
              {setup.voiceBindings && <Link to="/settings/sharing">{t('settings.mcp_title')}</Link>}
              <a href={setup.docs} target="_blank" rel="noopener noreferrer">
                {t('common.learn_more')}
              </a>
            </div>
          </section>
        )}
        <div className="integration-detail-grid">
          <section className="integration-detail-panel">
            <h3>{t('integrationCatalog.capabilitiesTitle')}</h3>
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
          <section className="integration-detail-panel integration-detail-action">
            <h3>{t('integrationCatalog.websiteTitle')}</h3>
            <p className="integration-detail-url">{entry.url}</p>
            <button type="button" onClick={openExternal} className="integration-open-button">
              {t('common.open')}
              <ExternalLinkIcon />
            </button>
          </section>
        </div>
      </main>
    </div>
  );
}
