import { useId, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { LockIcon, PhoneOutgoingIcon, ShieldCheckIcon, TriangleAlertIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { Profile } from '@/lib/api/types';
import { normalizePhone, phoneCountry, phoneProblem } from './phone';

export const MAX_MINUTES_CHOICES = [2, 5, 10, 15, 30] as const;

export interface CallDraft {
  to: string;
  brief: string;
  profileId: string;
  disclosureOn: boolean;
  /** Null until the user edits it: the settings template is used meanwhile. */
  disclosure: string | null;
  maxMinutes: number;
}

export const EMPTY_DRAFT: CallDraft = {
  to: '',
  brief: '',
  profileId: '',
  disclosureOn: true,
  disclosure: null,
  maxMinutes: 5,
};

const EXAMPLES = [
  ['calls.example_restaurant', 'calls.example_restaurant_brief'],
  ['calls.example_reschedule', 'calls.example_reschedule_brief'],
  ['calls.example_hours', 'calls.example_hours_brief'],
] as const;

export interface StartRequest {
  to: string;
  brief: string;
  profile_id: string;
  /** Omitted when unedited, so the backend renders the template itself. */
  disclosure?: string;
  max_minutes: number;
}

export function NewCallForm({
  draft,
  onChange,
  voices,
  disclosureTemplate,
  ready,
  starting,
  onStart,
}: {
  draft: CallDraft;
  onChange: (draft: CallDraft) => void;
  voices: Profile[];
  disclosureTemplate: string;
  /** Every readiness check passed. */
  ready: boolean;
  starting: boolean;
  onStart: (request: StartRequest) => void | Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const ids = useId();
  const [touched, setTouched] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const set = <K extends keyof CallDraft>(key: K, value: CallDraft[K]) =>
    onChange({ ...draft, [key]: value });

  const number = normalizePhone(draft.to);
  const problem = phoneProblem(draft.to);
  const country = problem ? null : phoneCountry(draft.to, i18n.language);
  const voice = voices.find((profile) => profile.id === draft.profileId) ?? null;
  const disclosure = draft.disclosure ?? disclosureTemplate;
  const brief = draft.brief.trim();
  const valid = !problem && Boolean(brief) && Boolean(voice);
  const blocked = !ready ? t('calls.blocked_setup') : !valid ? t('calls.blocked_form') : '';
  const showProblem = problem && (touched || (problem !== 'empty' && draft.to.length > 3));

  const request = (): StartRequest => ({
    to: number,
    brief,
    profile_id: draft.profileId,
    ...(!draft.disclosureOn
      ? { disclosure: '' }
      : // Only the switch opts out: a cleared line falls back to the template.
        draft.disclosure?.trim()
        ? { disclosure: draft.disclosure.trim() }
        : {}),
    max_minutes: draft.maxMinutes,
  });

  return (
    <form
      aria-label={t('calls.new_call')}
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        setTouched(true);
        if (ready && valid && !starting) setConfirming(true);
      }}
    >
      <div className="space-y-1.5">
        <label htmlFor={`${ids}-to`} className="text-sm font-medium">
          {t('calls.number_label')}
        </label>
        <Input
          id={`${ids}-to`}
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          spellCheck={false}
          placeholder="+1 555 010 0199"
          value={draft.to}
          aria-invalid={showProblem ? true : undefined}
          aria-describedby={`${ids}-to-hint`}
          onBlur={() => setTouched(true)}
          onChange={(event) => set('to', event.target.value)}
        />
        <p
          id={`${ids}-to-hint`}
          aria-live="polite"
          className={showProblem ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
        >
          {showProblem
            ? t(
                problem === 'missing_plus'
                  ? 'calls.number_missing_plus'
                  : problem === 'empty'
                    ? 'calls.number_required'
                    : 'calls.number_invalid',
              )
            : country
              ? t('calls.number_country', { country })
              : t('calls.number_hint')}
        </p>
      </div>

      <div className="space-y-1.5">
        <label htmlFor={`${ids}-brief`} className="text-sm font-medium">
          {t('calls.brief_label')}
        </label>
        <Textarea
          id={`${ids}-brief`}
          rows={4}
          value={draft.brief}
          placeholder={t('calls.brief_placeholder')}
          aria-describedby={`${ids}-examples`}
          onChange={(event) => set('brief', event.target.value)}
        />
        <div
          id={`${ids}-examples`}
          role="group"
          aria-label={t('calls.examples')}
          className="flex flex-wrap gap-1.5"
        >
          {EXAMPLES.map(([label, text]) => (
            <Button
              key={label}
              type="button"
              size="xs"
              variant="outline"
              className="rounded-full"
              onClick={() => set('brief', t(text))}
            >
              {t(label)}
            </Button>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <span id={`${ids}-voice`} className="text-sm font-medium">
          {t('calls.voice_label')}
        </span>
        {voices.length ? (
          <Select
            value={voice ? voice.id : null}
            onValueChange={(value) => set('profileId', String(value ?? ''))}
          >
            <SelectTrigger aria-labelledby={`${ids}-voice`} className="w-full">
              <SelectValue placeholder={t('calls.voice_placeholder')}>
                {voice ? voice.name : t('calls.voice_placeholder')}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {voices.map((profile) => (
                <SelectItem key={profile.id} value={profile.id}>
                  {profile.name}
                  <span className="ml-auto text-xs text-muted-foreground">
                    {t(profile.kind === 'design' ? 'calls.voice_designed' : 'calls.voice_verified')}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <p className="rounded-lg border border-dashed border-border px-3 py-2 text-sm text-muted-foreground">
            {t('calls.voice_none')}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {t('calls.voice_rule')}{' '}
          <Link to="/personas" className="text-primary underline-offset-2 hover:underline">
            {t('calls.verify_voice')}
          </Link>
          {' · '}
          <Link to="/design" className="text-primary underline-offset-2 hover:underline">
            {t('calls.design_voice')}
          </Link>
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <label
            htmlFor={`${ids}-disclosure`}
            className="flex items-center gap-1.5 text-sm font-medium"
          >
            {t('calls.first_line')}
            {draft.disclosureOn && (
              <LockIcon aria-hidden="true" className="size-3.5 text-muted-foreground" />
            )}
          </label>
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span aria-hidden="true">{t('calls.disclosure_toggle')}</span>
            <Switch
              size="sm"
              aria-label={t('calls.disclosure_toggle')}
              checked={draft.disclosureOn}
              onCheckedChange={(checked) => set('disclosureOn', checked)}
            />
          </span>
        </div>
        {draft.disclosureOn ? (
          <Textarea
            id={`${ids}-disclosure`}
            rows={2}
            value={disclosure}
            aria-describedby={`${ids}-disclosure-locked`}
            onChange={(event) => set('disclosure', event.target.value)}
          />
        ) : null}
        {draft.disclosureOn ? (
          <p id={`${ids}-disclosure-locked`} className="text-xs text-muted-foreground">
            {t('calls.disclosure_locked')}
          </p>
        ) : (
          <p className="flex gap-2 rounded-lg bg-warning-surface px-3 py-2 text-xs text-warning-foreground">
            <TriangleAlertIcon aria-hidden="true" className="size-4 shrink-0" />
            {t('calls.disclosure_off_warning')}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <span id={`${ids}-max`} className="text-sm font-medium">
          {t('calls.max_duration')}
        </span>
        <Select
          value={String(draft.maxMinutes)}
          onValueChange={(value) => set('maxMinutes', Number(value))}
        >
          <SelectTrigger aria-labelledby={`${ids}-max`} className="w-full sm:w-48">
            <SelectValue>{t('calls.minutes', { minutes: draft.maxMinutes })}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {MAX_MINUTES_CHOICES.map((minutes) => (
              <SelectItem key={minutes} value={String(minutes)}>
                {t('calls.minutes', { minutes })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <section
        aria-labelledby={`${ids}-plan`}
        className="space-y-2 rounded-xl border border-border/60 bg-muted/25 p-3 text-sm"
      >
        <h3 id={`${ids}-plan`} className="flex items-center gap-2 font-medium">
          <ShieldCheckIcon aria-hidden="true" className="size-4 text-primary" />
          {t('calls.plan_title')}
        </h3>
        <p className="whitespace-pre-wrap break-words text-muted-foreground">
          {brief || t('calls.plan_empty')}
        </p>
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {draft.disclosureOn && disclosure.trim() && <li>{t('calls.guard_disclosure')}</li>}
          <li>{t('calls.guard_payment')}</li>
          <li>{t('calls.guard_ask')}</li>
          <li>{t('calls.guard_max', { minutes: draft.maxMinutes })}</li>
        </ul>
      </section>

      <div className="space-y-1.5">
        <Button
          type="submit"
          size="lg"
          className="w-full"
          disabled={!ready || starting}
          aria-busy={starting}
          aria-describedby={blocked ? `${ids}-blocked` : undefined}
        >
          <PhoneOutgoingIcon aria-hidden="true" />
          {starting
            ? t('calls.starting')
            : problem
              ? t('calls.call_button_empty')
              : t('calls.call_button', { number })}
        </Button>
        {blocked && (
          <p id={`${ids}-blocked`} className="text-xs text-muted-foreground">
            {blocked}
          </p>
        )}
      </div>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{t('calls.confirm_title', { number })}</DialogTitle>
            <DialogDescription>
              {t('calls.confirm_body', { voice: voice?.name ?? '', minutes: draft.maxMinutes })}
            </DialogDescription>
          </DialogHeader>
          <p className="max-h-40 overflow-y-auto rounded-lg bg-muted/40 px-3 py-2 text-sm whitespace-pre-wrap break-words">
            {brief}
          </p>
          <DialogFooter>
            {/* Focus starts on Cancel: an Enter held from submitting the form
                must never dial by landing on the confirm button. */}
            <DialogClose render={<Button variant="outline" autoFocus />}>
              {t('common.cancel')}
            </DialogClose>
            <Button
              disabled={starting}
              onClick={async () => {
                setConfirming(false);
                await onStart(request());
              }}
            >
              <PhoneOutgoingIcon aria-hidden="true" />
              {t('calls.confirm_call')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </form>
  );
}
