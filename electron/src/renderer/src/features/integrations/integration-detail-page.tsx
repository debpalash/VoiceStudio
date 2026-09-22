import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { mcpSetup } from './mcp-setup';
import { n8nSetup } from './n8n-setup';
import { openaiAgentsSetup } from './openai-agents-setup';
import { saveLocalFile } from '@/lib/local-export';
import { describeError } from '@/lib/api/client';
import { ArrowLeftIcon, ExternalLinkIcon, BlocksIcon } from 'lucide-react';
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

export function IntegrationDetailPage() {
  const { t } = useTranslation();
  const { slug } = useParams({ strict: false });
  const backend = useBackendStatus();
  const [saving, setSaving] = useState(false);
  const workflow = useMemo(() => n8nSetup(slug ?? '', backend.baseUrl), [slug, backend.baseUrl]);
  const agents = openaiAgentsSetup(slug ?? '', backend.baseUrl);
  const setup = workflow ?? agents ?? mcpSetup(slug ?? '', backend.baseUrl);
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
            <p>{t('integrationCatalog.description')}</p>
          </div>
        </section>
        {setup && (
          <section className="integration-detail-panel space-y-3">
            <h3>{workflow || agents ? entry.name : t('settings.mcp_title')}</h3>
            <p>
              {agents
                ? t('integrationCatalog.openaiAgentsHint')
                : t(workflow ? 'integrationCatalog.n8nHint' : 'integrationCatalog.setupHint', {
                    file: setup.file,
                  })}
            </p>
            <pre className="max-h-80 overflow-auto rounded-lg bg-muted/40 p-4 text-xs">
              <code>{setup.text}</code>
            </pre>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(setup.text);
                    toast.success(t('transcriptions.copied'));
                  } catch {
                    toast.error(t('transcriptions.copy_failed'));
                  }
                }}
              >
                {t('transcriptions.copy')}
              </Button>
              {workflow ? (
                <Button
                  variant="outline"
                  disabled={saving}
                  aria-busy={saving}
                  onClick={async () => {
                    setSaving(true);
                    try {
                      const result = await saveLocalFile(
                        new Blob([setup.text], { type: 'application/json' }),
                        setup.file,
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
              ) : agents ? null : (
                <Link to="/settings/sharing">{t('settings.mcp_title')}</Link>
              )}
              <a href={setup.docs} target="_blank" rel="noopener noreferrer">
                {t('common.learn_more')}
              </a>
            </div>
          </section>
        )}
        <div className="integration-detail-grid">
          <section className="integration-detail-panel">
            <h3>{t('common.details')}</h3>
            <div className="integration-capabilities">
              {entry.detailKeys.map((key) => (
                <span key={key}>{t(key)}</span>
              ))}
            </div>
            <p className="integration-detail-note">{t('directoryExamples.notice')}</p>
          </section>
          <section className="integration-detail-panel integration-detail-action">
            <h3>{t('common.details')}</h3>
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
