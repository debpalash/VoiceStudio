import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  ArrowLeftRightIcon,
  LanguagesIcon,
  MessageSquareIcon,
  MicIcon,
  PhoneCallIcon,
  WandSparklesIcon,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  makeCallWorkflow,
  makeProcessingWorkflow,
  makeSpeechWorkflow,
  type WorkflowDocument,
} from './workflow-model';

interface Template {
  id: string;
  name: string;
  description: string;
  icon: typeof MicIcon;
  /** Builds the document and names the step whose settings open first. */
  create(): { document: WorkflowDocument; selectIndex: number };
}

export function workflowTemplates(t: TFunction): { group: string; items: Template[] }[] {
  const speech = (cleanup: boolean): Template => {
    const name = t(cleanup ? 'workflowRun.clean_recipe' : 'workflowRun.recipe');
    return {
      id: cleanup ? 'clean_recipe' : 'recipe',
      name,
      icon: cleanup ? WandSparklesIcon : MessageSquareIcon,
      description: t(cleanup ? 'workflows.template_clean_recipe' : 'workflows.template_recipe'),
      create: () => ({
        document: makeSpeechWorkflow(name, t('workflowRun.sample'), cleanup),
        selectIndex: 0,
      }),
    };
  };
  const processing = (
    id: 'transcription' | 'conversion' | 'localization',
    icon: typeof MicIcon,
  ): Template => {
    const name = t(`workflowRun.${id}_recipe`);
    const kinds =
      id === 'transcription'
        ? (['audio', 'transcribe', 'end'] as const)
        : id === 'conversion'
          ? (['audio', 'convert', 'end'] as const)
          : (['audio', 'transcribe', 'translate', 'speak', 'end'] as const);
    return {
      id,
      name,
      icon,
      description: t(`workflows.template_${id}`),
      create: () => ({ document: makeProcessingWorkflow(name, [...kinds]), selectIndex: 0 }),
    };
  };
  const call = (id: 'restaurant' | 'reschedule' | 'hours'): Template => {
    const name = t(`calls.example_${id}`);
    return {
      id,
      name,
      icon: PhoneCallIcon,
      description: t(`workflows.template_${id}`),
      create: () => ({
        document: makeCallWorkflow(name, t(`calls.example_${id}_brief`)),
        selectIndex: 1,
      }),
    };
  };
  return [
    { group: t('workflows.group_narration'), items: [speech(false), speech(true)] },
    {
      group: t('workflows.group_audio'),
      items: [
        processing('transcription', MicIcon),
        processing('conversion', ArrowLeftRightIcon),
        processing('localization', LanguagesIcon),
      ],
    },
    {
      group: t('workflows.group_calls'),
      items: [call('restaurant'), call('reschedule'), call('hours')],
    },
  ];
}

export function WorkflowTemplatePicker({
  open,
  onOpenChange,
  onPick,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  onPick(document: WorkflowDocument, selectIndex: number): void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" style={{ background: 'var(--popover)' }}>
        <DialogHeader>
          <DialogTitle>{t('workflows.template_title')}</DialogTitle>
          <DialogDescription>{t('workflows.template_hint')}</DialogDescription>
        </DialogHeader>
        <div className="workflow-template-groups studio-scrollbar">
          {workflowTemplates(t).map(({ group, items }) => (
            <section key={group} aria-label={group}>
              <h3>{group}</h3>
              <div className="workflow-template-grid">
                {items.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    className="workflow-template-card"
                    aria-label={template.name}
                    aria-describedby={`workflow-template-${template.id}`}
                    onClick={() => {
                      const { document, selectIndex } = template.create();
                      onPick(document, selectIndex);
                      onOpenChange(false);
                    }}
                  >
                    <span className="workflow-template-card__icon">
                      <template.icon size={16} aria-hidden="true" />
                    </span>
                    <span className="workflow-template-card__text">
                      <strong>{template.name}</strong>
                      <span id={`workflow-template-${template.id}`}>{template.description}</span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
