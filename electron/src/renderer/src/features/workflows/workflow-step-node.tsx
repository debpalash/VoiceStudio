import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import {
  ArrowLeftRightIcon,
  AudioLinesIcon,
  BotIcon,
  CircleAlertIcon,
  FlagIcon,
  GitBranchIcon,
  LanguagesIcon,
  MessageSquareIcon,
  MicIcon,
  PhoneCallIcon,
  PlayIcon,
  WandSparklesIcon,
} from 'lucide-react';
import {
  stepSetupIssue,
  type StepKind,
  type StepSetupIssue,
  type WorkflowStep,
} from './workflow-model';

export const STEP_ICONS: Record<StepKind, typeof PlayIcon> = {
  start: PlayIcon,
  agent: BotIcon,
  speak: MessageSquareIcon,
  condition: GitBranchIcon,
  call: PhoneCallIcon,
  normalize: WandSparklesIcon,
  audio: AudioLinesIcon,
  transcribe: MicIcon,
  translate: LanguagesIcon,
  convert: ArrowLeftRightIcon,
  end: FlagIcon,
};

export type CanvasNode = Node<{
  step: WorkflowStep;
  label: string;
  summary: string;
  issue: StepSetupIssue | null;
}>;

/** A short, complete description of what a step is set to do; empty when it still needs setup. */
export function stepSummary(step: WorkflowStep, t: TFunction, voiceName?: string): string {
  if (stepSetupIssue(step)) return '';
  switch (step.kind) {
    case 'audio':
      return `${t('workflowRun.add_audio')} · ${step.media?.length || 0}`;
    case 'translate':
      return [step.sourceLanguage, step.language].filter(Boolean).join(' → ');
    case 'start':
      return `${t('workflowRun.scripts')} · ${step.scripts?.length || step.text.split(/^\s*---\s*$/m).filter((text) => text.trim()).length}`;
    case 'end':
      return t('dub.export');
    case 'speak':
    case 'convert':
      return voiceName || '';
    case 'normalize':
      return `${step.targetDb ?? -2} dBFS`;
    case 'call':
      return [step.phone, step.text].filter(Boolean).join(' · ') || t('workflows.add_details');
    case 'condition':
      return `${t(`workflows.match_${step.match || 'contains'}`)} · ${step.text}`;
    default:
      return step.text || t('workflows.add_details');
  }
}

export function StepCard({ data, selected }: NodeProps<CanvasNode>) {
  const { t } = useTranslation();
  const Icon = STEP_ICONS[data.step.kind];
  const reason = data.issue ? t(`workflowRun.invalid_${data.issue}`) : '';
  return (
    <div
      className={`workflow-node workflow-node--${data.step.kind}${selected ? ' is-selected' : ''}${data.issue ? ' needs-setup' : ''}`}
    >
      {!['start', 'audio'].includes(data.step.kind) && (
        <Handle type="target" position={Position.Left} />
      )}
      <span className="workflow-node__icon">
        <Icon size={18} aria-hidden="true" />
      </span>
      <span className="workflow-node__body">
        {data.step.title && <small>{data.label}</small>}
        <strong title={data.step.title || data.label}>{data.step.title || data.label}</strong>
        {data.issue ? (
          <span className="workflow-node__badge" title={reason}>
            <CircleAlertIcon size={12} aria-hidden="true" />
            {t('workflows.needs_setup')}
          </span>
        ) : (
          data.summary && <span className="workflow-node__summary">{data.summary}</span>
        )}
      </span>
      {data.step.kind === 'condition' ? (
        <>
          <Handle
            type="source"
            id="yes"
            position={Position.Right}
            style={{ top: '35%' }}
            aria-label={t('common.yes')}
          />
          <Handle
            type="source"
            id="no"
            position={Position.Right}
            style={{ top: '70%' }}
            aria-label={t('common.no')}
          />
        </>
      ) : (
        data.step.kind !== 'end' && <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}

export const nodeTypes = { workflowStep: StepCard };
