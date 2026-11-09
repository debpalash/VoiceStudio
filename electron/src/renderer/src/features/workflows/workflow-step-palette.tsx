import { Fragment } from 'react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { StepKind } from './workflow-model';
import { STEP_ICONS } from './workflow-step-node';

/** Drag payload type for dropping a palette step onto the canvas. */
export const STEP_DRAG_TYPE = 'application/x-voicestudio-workflow-step';

const GROUPS: StepKind[][] = [
  ['start', 'audio'],
  ['speak', 'transcribe', 'translate', 'convert', 'normalize'],
  ['condition', 'agent', 'call'],
  ['end'],
];

/**
 * Floating step toolbar on the canvas edge. Click adds a step (after the
 * selected one when it has a free output); drag drops it where released.
 */
export function WorkflowStepPalette({
  disabled,
  after,
  onAdd,
}: {
  disabled: boolean;
  /** True when a click wires the new step after the selected step. */
  after: boolean;
  onAdd(kind: StepKind): void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="workflow-step-palette"
      role="toolbar"
      aria-orientation="vertical"
      aria-label={t('workflows.add_step')}
      onKeyDown={(event) => {
        if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
        const buttons = [
          ...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
        ];
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        if (index < 0) return;
        event.preventDefault();
        const next =
          event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? buttons.length - 1
              : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next].focus();
      }}
    >
      {GROUPS.map((group, index) => (
        <Fragment key={group[0]}>
          {index > 0 && <span className="workflow-step-palette__divider" aria-hidden="true" />}
          {group.map((kind) => {
            const Icon = STEP_ICONS[kind];
            const step = t(`workflows.step_${kind}`);
            const label = t(after ? 'workflows.add_after' : 'workflows.add_kind', { step });
            return (
              <Tooltip key={kind}>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      className={`workflow-step-palette__button workflow-palette-icon--${kind}`}
                      aria-label={label}
                      disabled={disabled}
                      draggable={!disabled}
                      onDragStart={(event) => {
                        event.dataTransfer.setData(STEP_DRAG_TYPE, kind);
                        event.dataTransfer.effectAllowed = 'copy';
                      }}
                      onClick={() => onAdd(kind)}
                    />
                  }
                >
                  <Icon size={16} aria-hidden="true" />
                </TooltipTrigger>
                <TooltipContent side="right">
                  <span className="grid gap-0.5">
                    <span>{label}</span>
                    {['agent', 'call'].includes(kind) && (
                      <span className="opacity-70">{t('workflows.local_draft')}</span>
                    )}
                    <span className="opacity-70">{t('workflows.palette_hint')}</span>
                  </span>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}
