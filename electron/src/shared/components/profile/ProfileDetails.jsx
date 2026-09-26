import { X, Check, Lock, Unlock, ShieldCheck, Square, Mic } from 'lucide-react';
import { Panel, Button, Input, Textarea, Field, Badge } from '../../ui';

/**
 * ProfileDetails — editable details panel + consent-lock panel for the
 * VoiceProfile page. Pure presentation; state/handlers live in the parent.
 */
export default function ProfileDetails({
  profile,
  editing,
  draft,
  setDraft,
  saving,
  cancelEdits,
  saveEdits,
  onUnlock,
  onRevokeConsent,
  consentStatement,
  consentRec,
  consentSubmitting,
  t,
}) {
  return (
    <div className="flex min-w-0 flex-col gap-[var(--space-4)]">
      {/* Editable details */}
      <Panel
        variant="flat"
        padding="md"
        className="!bg-transparent !shadow-none"
        title={<>{t('voice_profile.details')}</>}
        actions={
          editing ? (
            <>
              <Button variant="ghost" size="sm" onClick={cancelEdits} leading={<X size={12} />}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={saveEdits}
                loading={saving}
                leading={!saving && <Check size={12} />}
              >
                {t('common.save')}
              </Button>
            </>
          ) : null
        }
      >
        {editing ? (
          <div className="grid grid-cols-[1fr] gap-[var(--space-5)] @min-[700px]/voice-profile:grid-cols-[1fr_1fr]">
            <Field label={t('voice_profile.style_instruct')}>
              <Textarea
                rows={2}
                value={draft.instruct}
                onChange={(e) => setDraft({ ...draft, instruct: e.target.value })}
                placeholder={t('voice_profile.style_placeholder')}
              />
            </Field>
            <Field label={t('voice_profile.language')}>
              <Input
                value={draft.language}
                onChange={(e) => setDraft({ ...draft, language: e.target.value })}
                placeholder={t('clone.auto')}
              />
            </Field>
          </div>
        ) : (
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-[var(--space-6)] gap-y-[var(--space-3)] text-[var(--text-md)]">
            <dt className="text-fg-subtle">{t('voice_profile.style_instruct')}</dt>
            <dd className="m-0 max-w-[42ch] whitespace-pre-wrap break-words text-right text-fg">
              {profile.instruct || <em className="italic text-fg-subtle">{t('common.not_set')}</em>}
            </dd>
            <dt className="text-fg-subtle">{t('voice_profile.language')}</dt>
            <dd className="m-0 text-right text-fg">{profile.language || t('clone.auto')}</dd>
          </dl>
        )}
        {editing ? (
          <Field label={t('voice_profile.ref_transcript')} hint={t('voice_profile.ref_help')}>
            <Textarea
              rows={2}
              value={draft.ref_text}
              onChange={(e) => setDraft({ ...draft, ref_text: e.target.value })}
              placeholder={t('clone.optional')}
            />
          </Field>
        ) : profile.ref_text ? (
          <details className="mt-[var(--space-4)] rounded-[var(--radius-md)] bg-[var(--chrome-hover-bg)] p-[var(--space-3)]">
            <summary className="cursor-pointer text-[var(--text-sm)] font-medium text-fg-muted marker:text-fg-subtle">
              {t('voice_profile.ref_transcript')}
            </summary>
            <p className="mb-0 mt-[var(--space-3)] whitespace-pre-wrap break-words text-[var(--text-sm)] leading-[1.55] text-fg-muted">
              {profile.ref_text}
            </p>
          </details>
        ) : null}
        {profile.is_locked && !editing && (
          <div className="mt-[var(--space-4)] flex flex-wrap items-center gap-[var(--space-4)] rounded-[var(--radius-md)] border border-transparent bg-[rgba(250,189,47,0.06)] px-[var(--space-4)] py-[var(--space-3)]">
            <Badge tone="warn" dot>
              <Lock size={10} /> {t('voice_profile.locked')}
            </Badge>
            <span className="min-w-[200px] flex-1 text-fg-muted [font-size:var(--text-base)]">
              {t('voice_profile.locked_explain')}
            </span>
            <Button variant="subtle" size="sm" onClick={onUnlock} leading={<Unlock size={12} />}>
              {t('voice_profile.unlock')}
            </Button>
          </div>
        )}
      </Panel>

      {/* Consent lock (Wave 0.2) — verify this is your own voice */}
      <Panel
        variant="flat"
        padding="md"
        className="!bg-transparent !shadow-none"
        title={
          <>
            <ShieldCheck size={12} /> {t('voice_profile.consent_title')}
          </>
        }
      >
        {profile.verified_own_voice ? (
          <div className="mt-[var(--space-4)] flex flex-wrap items-center gap-[var(--space-4)] rounded-[var(--radius-md)] border border-transparent bg-[rgba(250,189,47,0.06)] px-[var(--space-4)] py-[var(--space-3)]">
            <Badge tone="success" dot>
              <ShieldCheck size={10} /> {t('voice_profile.verified')}
            </Badge>
            <span className="min-w-[200px] flex-1 text-fg-muted [font-size:var(--text-base)]">
              {t('voice_profile.consent_verified_explain', {
                date: profile.consent_recorded_at
                  ? new Date(profile.consent_recorded_at * 1000).toLocaleDateString()
                  : '',
              })}
            </span>
            <Button variant="subtle" size="sm" onClick={onRevokeConsent} leading={<X size={12} />}>
              {t('voice_profile.consent_revoke')}
            </Button>
          </div>
        ) : (
          <>
            <p className="m-0 text-[var(--text-sm)] leading-[1.5] text-fg-muted">
              {t('voice_profile.consent_explain')}
            </p>
            <details className="text-[var(--text-sm)] text-fg-muted">
              <summary className="cursor-pointer font-medium">
                {t('voice_profile.consent_record')}
              </summary>
              <blockquote className="mb-0 mt-[var(--space-3)] border-l-2 border-[var(--color-brand)] pl-[var(--space-3)] leading-[1.55]">
                “{consentStatement}”
              </blockquote>
            </details>
            {consentRec.isRecording ? (
              <Button
                variant="danger"
                size="sm"
                onClick={consentRec.stopRecording}
                leading={<Square size={12} />}
              >
                {t('voice_profile.consent_stop')} ({consentRec.recordingTime}s)
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                onClick={consentRec.startRecording}
                loading={consentSubmitting || consentRec.isCleaning}
                leading={!(consentSubmitting || consentRec.isCleaning) && <Mic size={12} />}
              >
                {t('voice_profile.consent_record')}
              </Button>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
