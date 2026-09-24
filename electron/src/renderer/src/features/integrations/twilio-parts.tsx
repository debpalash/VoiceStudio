import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { CheckIcon, CopyIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';

export type StepStatus = 'done' | 'todo' | 'blocked';
export type OverallStatus = 'setup' | 'ready' | 'live' | 'attention';

export function stepElementId(id: string) {
  return `twilio-step-${id}`;
}

/** Scrolls to a setup step and moves keyboard focus to its heading. */
export function focusStep(id: string) {
  const heading = document.getElementById(`${stepElementId(id)}-title`);
  heading?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  heading?.focus({ preventScroll: true });
}

export async function copyText(text: string, t: (key: string) => string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(t('transcriptions.copied'));
  } catch {
    toast.error(t('transcriptions.copy_failed'));
  }
}

export function StatusPill({ status }: { status: OverallStatus }) {
  const { t } = useTranslation();
  return (
    <span className="twilio-pill" data-status={status} role="status">
      <span className="twilio-pill-dot" aria-hidden />
      {t(`twilioIntegration.status_${status}`)}
    </span>
  );
}

export function StepBadge({ status }: { status: StepStatus }) {
  const { t } = useTranslation();
  return (
    <span className="twilio-step-badge" data-status={status}>
      {t(`twilioIntegration.step_${status}`)}
    </span>
  );
}

export function StepCard({
  id,
  index,
  title,
  description,
  status,
  children,
  footer,
  disabled,
}: {
  id: string;
  index: number;
  title: string;
  description: ReactNode;
  status: StepStatus;
  children: ReactNode;
  footer?: ReactNode;
  /** Read-only while an update is in flight, so its response cannot overwrite new edits. */
  disabled?: boolean;
}) {
  const base = stepElementId(id);
  return (
    <section
      id={base}
      className="twilio-step"
      data-status={status}
      aria-labelledby={`${base}-title`}
      aria-describedby={`${base}-description`}
    >
      <header className="twilio-step-header">
        <span className="twilio-step-index" aria-hidden>
          {status === 'done' ? <CheckIcon /> : index}
        </span>
        <div className="twilio-step-heading">
          <h3 id={`${base}-title`} tabIndex={-1}>
            {title}
          </h3>
          <p id={`${base}-description`}>{description}</p>
        </div>
        <StepBadge status={status} />
      </header>
      <fieldset className="twilio-step-fieldset" disabled={disabled}>
        <div className="twilio-step-body">{children}</div>
        {footer && <div className="twilio-step-footer">{footer}</div>}
      </fieldset>
    </section>
  );
}

/** A labelled read-only value (URL, command) with its own Copy button. */
export function CopyField({ label, value, hint }: { label: string; value: string; hint?: string }) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="twilio-copy">
      <span id={`${id}-label`} className="twilio-field-label">
        {label}
      </span>
      <div className="twilio-copy-row">
        <code aria-labelledby={`${id}-label`}>{value}</code>
        <Button
          size="sm"
          variant="outline"
          aria-label={t('twilioIntegration.copyLabel', { label })}
          onClick={() => void copyText(value, t)}
        >
          <CopyIcon aria-hidden />
          {t('transcriptions.copy')}
        </Button>
      </div>
      {hint && <small>{hint}</small>}
    </div>
  );
}

/** A field wrapper: visible label, control, optional hint wired to the control. */
export function Field({
  label,
  hint,
  children,
  htmlFor,
  wide,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  htmlFor: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? 'twilio-field twilio-field-wide' : 'twilio-field'}>
      <label htmlFor={htmlFor} className="twilio-field-label">
        {label}
      </label>
      {children}
      {hint && <small id={`${htmlFor}-hint`}>{hint}</small>}
    </div>
  );
}

/** Why an action is unavailable, announced with the control it explains. */
export function DisabledReason({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="twilio-reason">
      {children}
    </p>
  );
}
