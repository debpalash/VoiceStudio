import { ProfileConsent } from './profile-consent';
import { ProfilePreview } from './profile-preview';
import { ProfileUsagePanel } from './profile-usage';
import { LanguagePicker } from './language-picker';
import { PersonaExport } from './persona-export';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import {
  Clock3Icon,
  FileAudioIcon,
  ReplaceIcon,
  ShieldCheckIcon,
  Trash2Icon,
  Volume2Icon,
  XIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { WaveformPlayer } from '@/components/waveform-player';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { useDeleteProfile, useReplaceProfileAudio } from '@/hooks/use-profiles';
import { apiJson, describeError, profileAudioUrl } from '@/lib/api/client';
import type { Profile } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';
import { cloneSettingsStore, patchCloneSettings } from '@/lib/store/clone-settings';
import { createObjectUrl, revokeObjectUrl } from '@/lib/audio/object-url';
import { cn } from '@/lib/utils';
import { ReferenceSourcePicker, ReferenceUsageNote } from './reference-input';
import { ProfileImageEditor } from './profile-image-editor';
import { formatRelative } from './format';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';

export function EditProfile({ profile, onDone }: { profile: Profile; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const prefix = useId();
  const client = useQueryClient();
  const deleteProfile = useDeleteProfile();
  const replaceAudio = useReplaceProfileAudio();
  const ttsBlocker = useTtsReadiness();
  const [draft, setDraft] = useState({
    name: profile.name,
    language: profile.language || 'Auto',
    ref_text: profile.ref_text ?? '',
    instruct: profile.instruct ?? '',
  });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  // Replacing the reference (#2282): `choosing` shows the upload/record zones,
  // `replacement` holds the accepted clip until Save writes it to this profile.
  const [choosing, setChoosing] = useState(false);
  const [replacement, setReplacement] = useState<{
    file: File;
    durationSeconds: number | null;
  } | null>(null);
  const [replacementUrl, setReplacementUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!replacement) {
      setReplacementUrl(null);
      return;
    }
    const url = createObjectUrl(replacement.file);
    setReplacementUrl(url);
    return () => revokeObjectUrl(url);
  }, [replacement]);
  const canReplace = profile.kind !== 'design';
  // Transcript as it stood when Replace was pressed, so Keep reference
  // restores the user's unsaved edits rather than the stored text.
  const refTextBeforeReplace = useRef(draft.ref_text);
  const startReplacement = () => {
    refTextBeforeReplace.current = draft.ref_text;
    setChoosing(true);
  };
  const acceptReplacement = (file: File, durationSeconds: number | null) => {
    setReplacement({ file, durationSeconds });
    setChoosing(false);
    // The saved transcript describes the old clip. Leave it blank so the
    // backend transcribes the new one locally unless the user types it.
    setDraft((previous) => ({ ...previous, ref_text: '' }));
  };
  const keepCurrentReference = () => {
    setReplacement(null);
    setChoosing(false);
    setDraft((previous) => ({ ...previous, ref_text: refTextBeforeReplace.current }));
  };
  const [confirmDelete, setConfirmDelete] = useState(false);
  const unavailable = busy || deleteProfile.isPending;
  return (
    <form
      className="grid gap-4 @min-[620px]:grid-cols-2"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy || !draft.name.trim()) return;
        setBusy(true);
        setFailed(null);
        try {
          // With a new clip, the edits and its transcript travel in the one
          // audio request: the backend saves all of them or none, and the old
          // clip's text is never saved against the new reference.
          const { ref_text: refText, ...fields } = draft;
          const updated = replacement
            ? await replaceAudio.mutateAsync({
                id: profile.id,
                refAudio: replacement.file,
                refAudioName: replacement.file.name,
                refText,
                fields,
              })
            : await apiJson<Profile>(`/profiles/${encodeURIComponent(profile.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(draft),
              });
          if (cloneSettingsStore.state.selectedProfileId === profile.id)
            patchCloneSettings({
              language: updated.language || 'Auto',
              refText: updated.ref_text ?? '',
              instruct: updated.instruct ?? '',
            });
          await client.invalidateQueries({ queryKey: queryKeys.profiles });
          onDone();
        } catch (error) {
          setFailed(describeError(error));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="space-y-3 border-b border-border/50 pb-4 @min-[620px]:col-span-2">
        <ProfileImageEditor profile={{ ...profile, name: draft.name }} />
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {Boolean(profile.verified_own_voice) && (
            <span className="inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-1 text-success">
              <ShieldCheckIcon className="size-3" />
              {t('voice_profile.verified')}
            </span>
          )}
          {Boolean(profile.is_locked) && (
            <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-1 text-warning">
              {t('voice_profile.locked')}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <Clock3Icon className="size-3" />
            {formatRelative(profile.created_at, i18n.language)}
          </span>
        </div>
      </div>

      {(profile.ref_audio_path || profile.locked_audio_path || profile.kind === 'design') && (
        <section className="space-y-2 rounded-lg bg-muted/25 p-3 @min-[620px]:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              <Volume2Icon className="size-4 text-muted-foreground" />
              {t('clone.reference_audio')}
            </h3>
            {canReplace && !replacement && !choosing && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={unavailable}
                onClick={startReplacement}
              >
                <ReplaceIcon data-icon="inline-start" />
                {t('clone.replace_reference')}
              </Button>
            )}
            {canReplace && (replacement || choosing) && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={unavailable}
                onClick={keepCurrentReference}
              >
                <XIcon data-icon="inline-start" />
                {t('clone.keep_reference')}
              </Button>
            )}
          </div>
          {replacement ? (
            <div className="flex flex-col gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <FileAudioIcon
                  className="size-4 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {t('clone.new_reference', { name: replacement.file.name })}
                </span>
                {replacement.durationSeconds != null ? (
                  <span className="text-[length:var(--text-caption)] text-muted-foreground tabular-nums">
                    {t('clone.duration_seconds', {
                      seconds: replacement.durationSeconds.toFixed(1),
                    })}
                  </span>
                ) : null}
              </div>
              <ReferenceUsageNote durationSeconds={replacement.durationSeconds} />
              {replacementUrl ? (
                <WaveformPlayer
                  key={replacementUrl}
                  src={replacementUrl}
                  source={'profile-editor-replacement-' + profile.id}
                  height={44}
                  compact
                />
              ) : null}
            </div>
          ) : profile.ref_audio_path || profile.locked_audio_path ? (
            <WaveformPlayer
              key={profile.audio_url ?? profile.id}
              src={profileAudioUrl(profile.id, profile.audio_url)}
              source={'profile-editor-' + profile.id}
              height={44}
              compact
            />
          ) : (
            <AudioPreviewButton
              src={profileAudioUrl(profile.id, profile.audio_url)}
              source={'profile-editor-' + profile.id}
              activity="synthesis"
              disabled={Boolean(ttsBlocker)}
              disabledLabel={t('engines.none_ready_title')}
              onReady={() => void client.invalidateQueries({ queryKey: queryKeys.profiles })}
            />
          )}
          {choosing && !replacement && (
            <div className="flex flex-col gap-2">
              <ReferenceSourcePicker onAccept={acceptReplacement} />
            </div>
          )}
          {(replacement || choosing) && (
            <p role="note" className="text-xs text-muted-foreground">
              {t('clone.replace_reference_hint')}
            </p>
          )}
        </section>
      )}

      <div className="space-y-1.5">
        <Label htmlFor={`${prefix}-name`}>{t('clone.profile_name')}</Label>
        <Input
          id={`${prefix}-name`}
          value={draft.name}
          disabled={unavailable}
          required
          onChange={(event) => setDraft((previous) => ({ ...previous, name: event.target.value }))}
        />
      </div>
      {(['ref_text', 'instruct'] as const).map((field) => (
        <div key={field} className="space-y-1.5">
          <Label htmlFor={`${prefix}-${field}`}>
            {t(field === 'ref_text' ? 'clone.transcript' : 'clone.style')}
          </Label>
          <Textarea
            id={`${prefix}-${field}`}
            rows={3}
            value={draft[field]}
            disabled={unavailable}
            className="resize-y"
            onChange={(event) =>
              setDraft((previous) => ({ ...previous, [field]: event.target.value }))
            }
          />
        </div>
      ))}
      <div className="space-y-1.5">
        <p className="text-sm font-medium">{t('clone.language')}</p>
        <LanguagePicker
          value={draft.language}
          disabled={unavailable}
          onValueChange={(language) => setDraft((previous) => ({ ...previous, language }))}
        />
      </div>
      <div className="min-w-0">
        <PersonaExport profile={profile} disabled={unavailable} />
      </div>
      <div className="min-w-0">
        <ProfilePreview key={profile.id} profile={profile} ttsBlocker={ttsBlocker} />
      </div>
      <div className="min-w-0">
        <ProfileUsagePanel id={profile.id} />
      </div>
      <div className="min-w-0">
        <ProfileConsent key={profile.id + '-consent'} profile={profile} />
      </div>
      {failed && (
        <p role="alert" className="text-sm text-destructive @min-[620px]:col-span-2">
          {t('clone.save_failed', { message: failed })}
        </p>
      )}
      <div
        className={cn(
          'flex min-h-10 items-center gap-2 border-t border-border/50 pt-4',
          '@min-[620px]:col-span-2',
          confirmDelete && 'rounded-lg border border-destructive/20 bg-destructive/5 px-3 pb-3',
        )}
      >
        {confirmDelete ? (
          <>
            <p className="min-w-0 flex-1 text-xs text-muted-foreground">
              {t('clone.delete_profile_confirm', { name: profile.name })}
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={deleteProfile.isPending}
              onClick={() => setConfirmDelete(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={deleteProfile.isPending}
              onClick={async () => {
                try {
                  await deleteProfile.mutateAsync(profile.id);
                  onDone();
                } catch {
                  /* The shared mutation keeps the action retryable and reports the error. */
                }
              }}
            >
              <Trash2Icon />
              {t('common.delete')}
            </Button>
          </>
        ) : (
          <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mr-auto text-muted-foreground hover:text-destructive"
              disabled={unavailable}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2Icon />
              {t('common.delete')}
            </Button>
            <Button type="button" variant="ghost" disabled={unavailable} onClick={onDone}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={unavailable || !draft.name.trim()}>
              {t(busy ? 'preferences.loading' : 'clone.save')}
            </Button>
          </>
        )}
      </div>
    </form>
  );
}
