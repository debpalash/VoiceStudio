import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { CircleAlertIcon, PhoneCallIcon, Trash2Icon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { stepSetupIssue, type WorkflowStep } from './workflow-model';
import { WorkflowInputs } from './workflow-inputs';
import { STEP_ICONS } from './workflow-step-node';

/** Steps whose only free-text setting is the generic instructions/brief box. */
const TEXT_KINDS = ['agent', 'call'];

export function WorkflowInspector({
  step,
  focusToken,
  onChange,
  onRemove,
  onClose,
  onPrepareCall,
}: {
  step: WorkflowStep;
  /** Changes whenever the inspector should take keyboard focus (a keyboard selection). */
  focusToken: number;
  onChange(change: Partial<WorkflowStep>): void;
  onRemove(): void;
  onClose(): void;
  onPrepareCall(): void;
}) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focusToken) panel.current?.querySelector<HTMLElement>('input, textarea, button')?.focus();
  }, [focusToken]);
  const Icon = STEP_ICONS[step.kind];
  const issue = stepSetupIssue(step);
  return (
    <aside
      ref={panel}
      className="workflow-inspector studio-scrollbar"
      aria-label={t('workflows.inspector')}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !event.defaultPrevented) onClose();
      }}
    >
      <div className="workflow-panel-heading">
        <span className="flex min-w-0 items-center gap-2">
          <span className={`workflow-palette-icon workflow-palette-icon--${step.kind}`}>
            <Icon size={14} aria-hidden="true" />
          </span>
          <span className="truncate">{t(`workflows.step_${step.kind}`)}</span>
        </span>
        <Button
          size="icon-xs"
          variant="ghost"
          onClick={onClose}
          aria-label={t('common.close')}
          title={t('common.close')}
        >
          <XIcon />
        </Button>
      </div>
      <div className="workflow-inspector__content">
        {issue && (
          <p className="workflow-inspector__issue" role="status">
            <CircleAlertIcon size={14} aria-hidden="true" />
            {t(`workflowRun.invalid_${issue}`)}
          </p>
        )}
        <label htmlFor="workflow-step-title">{t('workflows.step_name')}</label>
        <Input
          id="workflow-step-title"
          value={step.title}
          placeholder={t(`workflows.step_${step.kind}`)}
          onChange={(event) => onChange({ title: event.target.value })}
        />
        {step.kind === 'call' && (
          <>
            <label htmlFor="workflow-step-phone">{t('calls.number_label')}</label>
            <Input
              id="workflow-step-phone"
              value={step.phone}
              onChange={(event) => onChange({ phone: event.target.value })}
            />
          </>
        )}
        {TEXT_KINDS.includes(step.kind) && (
          <>
            <label htmlFor="workflow-step-text">
              {t(step.kind === 'call' ? 'calls.brief_label' : 'workflows.instructions')}
            </label>
            <Textarea
              id="workflow-step-text"
              value={step.text}
              onChange={(event) => onChange({ text: event.target.value })}
              rows={5}
            />
          </>
        )}
        <WorkflowInputs key={step.id} step={step} onChange={onChange} />
        {step.kind === 'call' && (
          <Button onClick={onPrepareCall}>
            <PhoneCallIcon aria-hidden="true" />
            {t('workflows.prepare_call')}
          </Button>
        )}
        <Button variant="ghost" onClick={onRemove}>
          <Trash2Icon aria-hidden="true" />
          {t('workflows.remove_step')}
        </Button>
      </div>
    </aside>
  );
}
