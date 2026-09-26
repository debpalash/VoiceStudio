import { useTranslation } from 'react-i18next';
import {
  UploadCloud,
  Wand2,
  FileText,
  Pencil,
  Sparkles,
  Download,
  Check,
  Loader,
} from 'lucide-react';

// ── Pipeline stepper ─────────────────────────────────────────────────────
// One legible spine for the whole dub journey so the user always knows where
// they are: Upload → Prepare → Transcribe → Edit → Generate → Export.
const DUB_PIPELINE = [
  { id: 'upload', key: 'dub.phase_upload', fallback: 'Upload', Icon: UploadCloud },
  { id: 'prepare', key: 'dub.phase_prepare', fallback: 'Prepare', Icon: Wand2 },
  { id: 'transcribe', key: 'dub.phase_transcribe', fallback: 'Transcribe', Icon: FileText },
  { id: 'edit', key: 'dub.phase_edit', fallback: 'Edit', Icon: Pencil },
  { id: 'generate', key: 'dub.phase_generate', fallback: 'Generate', Icon: Sparkles },
  { id: 'export', key: 'dub.phase_export', fallback: 'Export', Icon: Download },
];
const DUB_PHASE_BY_STEP = {
  idle: 0,
  uploading: 1,
  'installing-asr': 2,
  transcribing: 2,
  editing: 3,
  generating: 4,
  stopping: 4,
  done: 5,
};

function DubPipelineStepper({
  dubStep,
  inline = false,
  variant,
  selectableSteps = [],
  onStepSelect,
}) {
  const { t } = useTranslation();
  const current = DUB_PHASE_BY_STEP[dubStep] ?? 0;
  const busy =
    dubStep === 'uploading' ||
    dubStep === 'installing-asr' ||
    dubStep === 'transcribing' ||
    dubStep === 'generating' ||
    dubStep === 'stopping';
  return (
    <div
      className={[
        'dub-stepper',
        inline ? 'dub-stepper--inline' : '',
        variant === 'command' ? 'dub-stepper--command' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      role="list"
      aria-label={t('dub.pipeline', { defaultValue: 'Dubbing pipeline' })}
    >
      {DUB_PIPELINE.map((p, i) => {
        const done = i < current;
        const active = i === current;
        const spinning = active && busy;
        const Icon = done ? Check : spinning ? Loader : p.Icon;
        const label = t(p.key, { defaultValue: p.fallback });
        const selectable = !active && selectableSteps.includes(p.id) && onStepSelect;
        const content = (
          <>
            <span className="dub-stepper__icon">
              <Icon size={13} className={spinning ? 'dub-stepper__spin' : ''} aria-hidden="true" />
            </span>
            <span className="dub-stepper__label">{label}</span>
          </>
        );
        return (
          <div
            key={p.id}
            role="listitem"
            className={[
              'dub-stepper__step',
              done ? 'is-done' : '',
              active ? 'is-active' : '',
              i <= current ? 'is-reached' : '',
            ]
              .filter(Boolean)
              .join(' ')}
          >
            {selectable ? (
              <button
                type="button"
                className="dub-stepper__action"
                onClick={() => onStepSelect(p.id)}
                title={label}
              >
                {content}
              </button>
            ) : (
              <span className="dub-stepper__action" aria-current={active ? 'step' : undefined}>
                {content}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default DubPipelineStepper;
