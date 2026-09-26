import {
  ArrowLeft,
  Pencil,
  Download,
  Trash2,
  ShieldCheck,
  Lock,
  Clock,
  Volume2,
} from 'lucide-react';
import { Button, Input, Badge } from '../../ui';
import WaveformPlayer from '../WaveformPlayer';

/**
 * ProfileHeader — toolbar + hero (identity) for the VoiceProfile page.
 * Pure presentation; all state/handlers live in the parent VoiceProfile.
 */
export default function ProfileHeader({
  profile,
  isDesign,
  TypeIcon,
  onBack,
  editing,
  setEditing,
  includeReference,
  setIncludeReference,
  onExportPersona,
  exporting,
  onDelete,
  draft,
  setDraft,
  createdDate,
  audioUrl,
  t,
}) {
  return (
    <>
      {/* Toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-[var(--space-2)]">
        <Button
          variant="ghost"
          size="sm"
          className="!size-[28px] !p-0"
          onClick={onBack}
          aria-label={t('common.back')}
          title={t('common.back')}
        >
          <ArrowLeft size={13} />
        </Button>
        <span className="inline-flex items-center gap-[var(--space-2)] text-fg-subtle [font-size:var(--text-xs)] font-semibold uppercase tracking-[0.06em]">
          <TypeIcon size={12} />{' '}
          {isDesign ? t('voice_profile.designed') : t('voice_profile.cloned')}
        </span>
        <div className="flex-1" />
        {!editing && (
          <Button
            variant="subtle"
            size="sm"
            onClick={() => setEditing(true)}
            leading={<Pencil size={12} />}
          >
            {t('voice_profile.edit')}
          </Button>
        )}
        {!editing && (
          <label
            className="inline-flex items-center gap-1 text-[11px] text-fg-muted"
            title={t('voice_profile.persona_include_ref_hint', {
              defaultValue:
                'Include the raw reference clip. Off = share only a watermarked preview (recommended).',
            })}
          >
            <input
              type="checkbox"
              checked={includeReference}
              onChange={(e) => setIncludeReference(e.target.checked)}
            />
            {t('voice_profile.persona_include_ref', { defaultValue: 'Include voice clip' })}
          </label>
        )}
        {!editing && (
          <Button
            variant="subtle"
            size="sm"
            className="!size-[28px] !p-0"
            onClick={onExportPersona}
            loading={exporting}
            aria-label={t('voice_profile.persona_export', { defaultValue: 'Export persona' })}
            title={t('voice_profile.persona_export', { defaultValue: 'Export persona' })}
          >
            {!exporting && <Download size={12} />}
          </Button>
        )}
        <Button
          variant="danger"
          size="sm"
          className="!size-[28px] !p-0"
          onClick={onDelete}
          aria-label={t('common.delete')}
          title={t('common.delete')}
        >
          <Trash2 size={12} />
        </Button>
      </div>

      {/* Hero */}
      <section className="voice-profile__hero pb-[var(--space-5)]">
        <div className="flex w-full flex-wrap items-center gap-[var(--space-5)]">
          <div className="flex min-w-[260px] flex-1 items-center gap-[var(--space-4)]">
            <div
              className={`flex h-[54px] w-[54px] shrink-0 items-center justify-center rounded-[16px_20px_14px_22px/18px_14px_22px_16px] border ${
                isDesign
                  ? 'border-transparent bg-[rgba(142,192,124,0.15)] text-success'
                  : 'border-transparent bg-[rgba(211,134,155,0.15)] text-brand'
              }`}
            >
              <TypeIcon size={22} />
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-[var(--space-2)]">
              {editing ? (
                <Input
                  size="lg"
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder={t('voice_profile.name_placeholder')}
                  aria-label={t('voice_profile.name_placeholder')}
                  autoFocus
                />
              ) : (
                <h1 className="m-0 text-pretty [font-family:var(--font-display)] [font-size:var(--text-2xl)] font-bold tracking-[-0.02em] text-fg">
                  {profile.name}
                </h1>
              )}
              <div className="flex flex-wrap gap-[var(--space-2)]">
                {!!profile.verified_own_voice && (
                  <Badge tone="success" dot>
                    <ShieldCheck size={10} /> {t('voice_profile.verified')}
                  </Badge>
                )}
                {profile.is_locked ? (
                  <Badge tone="warn" dot>
                    <Lock size={10} /> {t('voice_profile.locked')}
                  </Badge>
                ) : null}
                {profile.language && profile.language !== 'Auto' && (
                  <Badge tone="info">{profile.language}</Badge>
                )}
                <span className="inline-flex items-center gap-[4px] text-[0.65rem] text-fg-subtle">
                  <Clock size={9} /> {createdDate}
                </span>
              </div>
            </div>
          </div>

          {(profile.ref_audio_path || profile.locked_audio_path) && (
            <div className="flex min-w-[260px] flex-1 flex-col gap-[var(--space-2)] rounded-[var(--radius-md)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)]">
              <div className="inline-flex items-center gap-[var(--space-2)] [font-size:var(--text-xs)] font-semibold uppercase tracking-[0.05em] text-fg-subtle">
                <Volume2 size={11} />{' '}
                {profile.is_locked ? t('voice_profile.locked_ref') : t('voice_profile.ref_audio')}
              </div>
              <WaveformPlayer
                src={audioUrl}
                source="profile-ref"
                className="w-full max-w-[480px]"
              />
            </div>
          )}
        </div>
      </section>
    </>
  );
}
