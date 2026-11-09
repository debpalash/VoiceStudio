import { useTranslation } from 'react-i18next';
import { CheckIcon, CircleAlertIcon, CopyIcon, PlayIcon, Trash2Icon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { StepSetupIssue, WorkflowStep } from './workflow-model';

/** Inline-editable workflow name, autosave status and the workflow's actions. */
export function WorkflowTitleBar({
  name,
  running,
  saved,
  blocked,
  canDelete,
  onRename,
  onRestoreName,
  onSetUp,
  onRun,
  onDuplicate,
  onDelete,
}: {
  name: string;
  running: boolean;
  saved: boolean;
  /** The first step that still needs setup, which keeps Run unavailable. */
  blocked: { step: WorkflowStep; issue: StepSetupIssue } | null;
  canDelete: boolean;
  onRename(name: string): void;
  onRestoreName(): void;
  onSetUp(step: WorkflowStep): void;
  onRun(): void;
  onDuplicate(): void;
  onDelete(): void;
}) {
  const { t } = useTranslation();
  return (
    <div className="workflow-intro">
      <div className="workflow-intro__title">
        <div className="workflow-title-row">
          <input
            className="workflow-title-input"
            aria-label={t('workflows.name_label')}
            value={name}
            disabled={running}
            maxLength={120}
            onChange={(event) => onRename(event.target.value)}
            onBlur={() => {
              if (!name.trim()) onRestoreName();
            }}
          />
          {saved && (
            <span className="workflow-saved" title={t('workflows.saved_hint')}>
              <CheckIcon size={12} aria-hidden="true" />
              {t('workflows.saved')}
            </span>
          )}
        </div>
        <p>{t('workflows.subtitle')}</p>
      </div>
      <div className="workflow-intro__actions">
        {blocked && (
          <button
            type="button"
            id="workflow-run-blocked"
            className="workflow-run-blocked"
            disabled={running}
            title={t(`workflowRun.invalid_${blocked.issue}`)}
            onClick={() => onSetUp(blocked.step)}
          >
            <CircleAlertIcon size={13} aria-hidden="true" />
            {t('workflows.run_blocked', {
              step: blocked.step.title || t(`workflows.step_${blocked.step.kind}`),
            })}
          </button>
        )}
        <Button
          size="sm"
          disabled={Boolean(blocked)}
          aria-describedby={blocked ? 'workflow-run-blocked' : undefined}
          onClick={onRun}
        >
          <PlayIcon size={15} aria-hidden="true" />
          {t('workflowRun.run')}
        </Button>
        <Button size="sm" variant="outline" disabled={running} onClick={onDuplicate}>
          <CopyIcon size={15} aria-hidden="true" />
          {t('workflows.duplicate')}
        </Button>
        <Button size="sm" variant="outline" onClick={onDelete} disabled={running || !canDelete}>
          <Trash2Icon size={15} aria-hidden="true" />
          {t('workflows.delete')}
        </Button>
      </div>
    </div>
  );
}
