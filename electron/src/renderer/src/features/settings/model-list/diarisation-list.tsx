import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import type { EngineSelectionResult } from '@/lib/engine-selection-feedback';
import { useModelCatalogue } from '../model-catalogue-query';
import { familyIcons } from '../model-family';
import { ModelLicense } from '../model-license';
import { SettingsActionError } from '../settings-action-error';
import { familyModels, modelSearchText, modelsForEngine, unclaimedModels } from './linking';
import { useModelOps } from './model-ops';
import { describeJob } from './model-job';
import { ModelActions } from './model-actions';
import { ModelFailure, ModelGatedAccess } from './model-notices';
import { SelectButton, modelFamilyItem } from './model-line';
import { ActiveBadge, RecommendedBadge, StatusBadge } from './badges';
import { FamilyList } from './family-list';
import type { FamilyItem } from './family-row';
import { useSelection } from './use-engine-select';

interface DiarisationOption {
  id: string;
  label: string;
  model?: string | null;
  model_installed?: boolean;
  runtime_installed?: boolean;
  installed: boolean;
  reason?: string | null;
}

interface RuntimeStatus {
  installed: boolean;
  supported: boolean;
  install_allowed?: boolean;
  job?: { state: string; progress: number; error?: string | null };
}

const SORTFORMER = 'audiocpp-sortformer';

/** Settings -> Models -> Diarisation: each option with its weights and runtime. */
export function DiarisationSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const catalogue = useModelCatalogue();
  const ops = useModelOps({ trackResidency: false });
  const selection = useSelection('diarisation', [
    'diarisation-status',
    'sidebar-diarisation',
    'loaded-models',
  ]);
  const [starting, setStarting] = useState(false);
  const [runtimeFailed, setRuntimeFailed] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['diarisation-status'],
    queryFn: () =>
      apiJson<{ active: string; options: DiarisationOption[] }>('/engines/diarisation'),
  });
  const runtime = useQuery({
    queryKey: ['audiocpp-runtime-install'],
    queryFn: () => apiJson<RuntimeStatus>('/engines/audiocpp/runtime/install/status'),
    refetchInterval: (state) => (state.state.data?.job?.state === 'running' ? 1_500 : 10_000),
  });
  useEffect(() => {
    if (!runtime.data?.installed) return;
    void Promise.all([
      client.invalidateQueries({ queryKey: ['diarisation-status'] }),
      client.invalidateQueries({ queryKey: ['sidebar-diarisation'] }),
      client.invalidateQueries({ queryKey: queryKeys.engines }),
    ]);
  }, [client, runtime.data?.installed]);
  const runtimeRunning = starting || runtime.data?.job?.state === 'running';
  const installRuntime = async () => {
    if (runtime.data?.install_allowed === false) return;
    setStarting(true);
    setRuntimeFailed(null);
    try {
      await apiJson('/engines/audiocpp/runtime/install', { method: 'POST' });
      await runtime.refetch();
    } catch (error) {
      setRuntimeFailed(describeError(error));
    } finally {
      setStarting(false);
    }
  };
  const select = (id: string) =>
    selection.run(() =>
      apiJson<EngineSelectionResult>('/engines/diarisation/select', {
        method: 'POST',
        body: JSON.stringify({ engine_id: id }),
      }),
    );
  const models = familyModels(catalogue.data?.models, 'diarisation');
  const busy = selection.busy || runtimeRunning;

  const optionItem = (option: DiarisationOption): FamilyItem => {
    const active = query.data?.active === option.id;
    const linked = modelsForEngine(option.id, models);
    const model = linked[0];
    const info = model ? describeJob(model, ops) : undefined;
    const runtimeMissing = option.id === SORTFORMER && !option.runtime_installed;
    const selectButton = option.installed ? (
      <SelectButton active={active} disabled={busy} onSelect={() => void select(option.id)} />
    ) : null;
    return {
      key: 'diarisation:' + option.id,
      anchorId: 'diarisation-engine-' + option.id,
      name: option.label,
      summary: [option.reason, model ? undefined : option.model].filter(Boolean).join(' · '),
      badges: (
        <>
          {active && <ActiveBadge />}
          {model?.curated && <RecommendedBadge />}
          {model && (
            <ModelLicense
              repoId={model.repo_id}
              label={model.label}
              info={model.license_info}
              acceptance={model.license_acceptance}
            />
          )}
          {!option.installed && (
            <StatusBadge tone="muted" title={option.reason || undefined}>
              {t('modelSettings.unavailable')}
            </StatusBadge>
          )}
        </>
      ),
      actions: (
        <>
          {runtimeMissing && option.model_installed && runtime.data?.supported === true && (
            <Button
              size="sm"
              variant="outline"
              disabled={runtimeRunning || runtime.data.install_allowed === false}
              title={
                runtime.data.install_allowed === false
                  ? t('engines.localInstallRequired')
                  : undefined
              }
              onClick={() => void installRuntime()}
            >
              <DownloadIcon />
              {runtimeRunning
                ? `${t('modelMaintenance.installing')} ${Math.round((runtime.data.job?.progress || 0) * 100)}%`
                : t('modelMaintenance.install')}
            </Button>
          )}
          {model && info ? (
            <ModelActions
              model={model}
              ops={ops}
              info={info}
              maintenance
              select={model.installed ? selectButton : undefined}
            />
          ) : null}
          {(!model || !model.installed) && selectButton}
          {!model && !option.installed && !runtimeMissing && (
            <span className="text-xs text-muted-foreground">
              {option.model_installed
                ? t('modelMaintenance.installed')
                : t('modelSettings.unavailable')}
            </span>
          )}
        </>
      ),
      notices:
        (runtimeMissing && runtime.data?.install_allowed === false) ||
        (model && info && ((model.gated && !model.installed) || info.failed)) ? (
          <>
            {runtimeMissing && runtime.data?.install_allowed === false && (
              <p className="basis-full text-xs text-muted-foreground">
                {t('engines.localInstallRequired')}
              </p>
            )}
            {model && info && (
              <>
                <ModelGatedAccess model={model} ops={ops} info={info} />
                <ModelFailure model={model} ops={ops} info={info} />
              </>
            )}
          </>
        ) : undefined,
      search: [option.label, option.id, option.model, option.reason],
      childSearch: linked.flatMap(modelSearchText),
      rank: { active, ready: option.installed, recommended: Boolean(model?.curated) },
    };
  };
  const options = query.data ? (query.data.options ?? []) : undefined;
  const items = options && [
    ...options.map(optionItem),
    ...unclaimedModels(
      models,
      options.flatMap((option) => modelsForEngine(option.id, models)),
    ).map((model) => modelFamilyItem({ model, ops })),
  ];
  const failure = selection.failed || runtimeFailed;
  return (
    <>
      <FamilyList
        title={t('engineSidebar.diarisation')}
        icon={familyIcons.diarisation}
        items={items}
        error={
          query.isError ? (
            <SettingsActionError
              className="m-4"
              title={t('modelSettings.failed')}
              detail={describeError(query.error)}
              action={
                <Button size="xs" variant="ghost" onClick={() => void query.refetch()}>
                  {t('backend.retry')}
                </Button>
              }
            />
          ) : undefined
        }
        footer={
          <>
            {runtime.isError && (
              <SettingsActionError
                className="m-4"
                title={t('modelMaintenance.failed')}
                detail={describeError(runtime.error)}
                action={
                  <Button size="xs" variant="ghost" onClick={() => void runtime.refetch()}>
                    {t('backend.retry')}
                  </Button>
                }
              />
            )}
            {failure && (
              <SettingsActionError
                className="m-4"
                title={t('modelSettings.failed')}
                detail={failure}
                onDismiss={() => {
                  selection.clearFailure();
                  setRuntimeFailed(null);
                }}
              />
            )}
            {runtime.data?.job?.state === 'error' && runtime.data.job?.error && (
              <SettingsActionError
                className="m-4"
                title={t('modelMaintenance.failed')}
                detail={runtime.data.job?.error}
              />
            )}
          </>
        }
      />
      {ops.confirmDialog}
    </>
  );
}
