import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { CatalogueModel } from '../model-catalogue-query';
import { ModelLicense } from '../model-license';
import type { ModelOps } from './model-ops';
import { describeJob } from './model-job';
import { ModelActions } from './model-actions';
import { ModelFailure, ModelGatedAccess } from './model-notices';
import { ActiveBadge, RecommendedBadge } from './badges';
import { modelSearchText, type ListRank } from './linking';
import type { FamilyItem } from './family-row';

export function modelAnchor(repoId: string) {
  return 'model-' + repoId.replaceAll('/', '-');
}

/**
 * Select button shared by engines and models. The selected row already shows
 * a "Selected" badge, so the active state renders no second, disabled button.
 */
export function SelectButton({
  active,
  disabled,
  onSelect,
  loading,
}: {
  active: boolean;
  disabled?: boolean;
  onSelect: () => void;
  loading?: ReactNode;
}) {
  const { t } = useTranslation();
  if (active) return null;
  return (
    <Button size="sm" className="min-w-20" variant="outline" disabled={disabled} onClick={onSelect}>
      {loading}
      {t('modelSettings.select')}
    </Button>
  );
}

function ModelBadges({ model, active }: { model: CatalogueModel; active: boolean }) {
  return (
    <>
      {active && <ActiveBadge />}
      {model.curated && <RecommendedBadge />}
      <ModelLicense
        repoId={model.repo_id}
        label={model.label}
        info={model.license_info}
        acceptance={model.license_acceptance}
        dependencies={model.license_dependencies}
      />
    </>
  );
}

/** A model an engine hosts, listed inside the engine's expanded row. */
export function ModelLine({
  model,
  ops,
  active = false,
  select,
  anchorId = modelAnchor(model.repo_id),
}: {
  model: CatalogueModel;
  ops: ModelOps;
  active?: boolean;
  select?: ReactNode;
  /** Unique per page: one checkpoint can be listed under several engines. */
  anchorId?: string;
}) {
  const info = describeJob(model, ops);
  return (
    <div
      data-slot="model-line"
      data-active={active || undefined}
      className="flex min-w-0 flex-col gap-2 py-1.5 @2xl:flex-row @2xl:flex-wrap @2xl:items-center @2xl:gap-x-4"
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <h4
            id={anchorId}
            tabIndex={-1}
            className="min-w-0 text-[13px] outline-none [overflow-wrap:anywhere]"
          >
            {model.label}
          </h4>
          <ModelBadges model={model} active={active} />
        </div>
        <p className="truncate text-[11px] text-muted-foreground" title={model.repo_id}>
          {model.repo_id}
        </p>
      </div>
      <div
        role="group"
        aria-labelledby={anchorId}
        className="flex min-w-0 flex-wrap items-center gap-2"
      >
        <ModelActions model={model} ops={ops} info={info} select={select} />
      </div>
      <ModelGatedAccess model={model} ops={ops} info={info} />
      <ModelFailure model={model} ops={ops} info={info} />
    </div>
  );
}

/** A model no engine on this page claims, as a row of its own. */
export function modelFamilyItem({
  model,
  ops,
  active = false,
  select,
  summary,
  rank,
}: {
  model: CatalogueModel;
  ops: ModelOps;
  active?: boolean;
  select?: ReactNode;
  summary?: ReactNode;
  rank?: Partial<ListRank>;
}): FamilyItem {
  const info = describeJob(model, ops);
  return {
    key: 'model:' + model.repo_id,
    anchorId: modelAnchor(model.repo_id),
    name: model.label,
    summary: summary ?? model.note ?? model.repo_id,
    badges: <ModelBadges model={model} active={active} />,
    actions: <ModelActions model={model} ops={ops} info={info} select={select} />,
    notices:
      (model.gated && !model.installed) || info.failed ? (
        <>
          <ModelGatedAccess model={model} ops={ops} info={info} />
          <ModelFailure model={model} ops={ops} info={info} />
        </>
      ) : undefined,
    search: modelSearchText(model),
    rank: {
      active,
      ready: model.installed,
      recommended: Boolean(model.curated || model.required),
      ...rank,
    },
    folded: model.supported === false,
    installOnly: !select,
  };
}
