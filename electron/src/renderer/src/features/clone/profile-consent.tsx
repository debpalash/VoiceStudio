import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { MicIcon, SquareIcon, ShieldCheckIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { RecordingInputs } from '@/components/recording-inputs';
import { useRecording } from '@/hooks/use-recording';
import { apiJson, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import type { Profile } from '@/lib/api/types';

/** "This is my voice" / "I have the speaker's permission": a voice syncs only with one of these. */
function SyncConsent({ profile }: { profile: Profile }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const path = `/profiles/${encodeURIComponent(profile.id)}/attestation`;
  const send = async (basis: 'own_voice' | 'speaker_permission' | null) => {
    setBusy(true);
    setFailed(null);
    try {
      await apiJson(
        path,
        basis ? { method: 'POST', body: JSON.stringify({ basis }) } : { method: 'DELETE' },
      );
      await client.invalidateQueries({ queryKey: queryKeys.profiles });
    } catch (error) {
      setFailed(describeError(error));
    } finally {
      setBusy(false);
    }
  };
  const basis = profile.consent_attested_at ? profile.consent_basis : null;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">{t('voice_profile.attest_explain')}</p>
      {basis === 'own_voice' || basis === 'speaker_permission' ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <ShieldCheckIcon className="size-4 text-success" />
          <span>
            {t(
              basis === 'own_voice'
                ? 'voice_profile.attested_own_voice'
                : 'voice_profile.attested_permission',
            )}
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => void send(null)}
          >
            {t('voice_profile.attest_clear')}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void send('own_voice')}
          >
            {t('voice_profile.attest_own_voice')}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void send('speaker_permission')}
          >
            {t('voice_profile.attest_permission')}
          </Button>
        </div>
      )}
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {t('voice_profile.consent_failed', { message: failed })}
        </p>
      )}
    </div>
  );
}

export function ProfileConsent({ profile }: { profile: Profile }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<'revoke' | 'unlock' | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const change = async (action: 'record' | 'revoke' | 'unlock', file?: File) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setFailed(null);
    try {
      const body = file ? new FormData() : undefined;
      if (body && file) {
        body.append('consent_audio', file);
        body.append('consent_text', t('voice_profile.consent_statement'));
      }
      await apiJson(
        `/profiles/${encodeURIComponent(profile.id)}/${action === 'unlock' ? 'unlock' : 'consent'}`,
        {
          method: action === 'revoke' ? 'DELETE' : 'POST',
          body,
          signal: controller.signal,
        },
      );
      await client.invalidateQueries({ queryKey: queryKeys.profiles });
      if (!controller.signal.aborted) {
        toast.success(
          t(
            action === 'record'
              ? 'voice_profile.consent_saved'
              : action === 'revoke'
                ? 'voice_profile.consent_revoked'
                : 'voice_profile.unlocked',
          ),
        );
        setConfirm(null);
      }
    } catch (error) {
      if (!controller.signal.aborted) setFailed(describeError(error));
    } finally {
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  const rec = useRecording((file) => void change('record', file), false);
  const recordingBusy = rec.isRecording || rec.isStarting || rec.isCleaning;
  const disabled = busy || recordingBusy;
  const recordedAt = Number(profile.consent_recorded_at);
  return (
    <details className="space-y-3 border-t border-border/50 pt-4">
      <summary className="cursor-pointer text-sm font-medium">
        {t('voice_profile.consent_title')}
      </summary>
      <SyncConsent profile={profile} />
      {profile.verified_own_voice ? (
        <>
          <p className="flex items-center gap-2 text-sm">
            <ShieldCheckIcon className="size-4 text-success" />
            {t('voice_profile.verified')}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('voice_profile.consent_verified_explain', {
              date: recordedAt ? new Date(recordedAt * 1000).toLocaleDateString() : '',
            })}
          </p>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => setConfirm('revoke')}
          >
            {t('voice_profile.consent_revoke')}
          </Button>
        </>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">{t('voice_profile.consent_explain')}</p>
          <blockquote className="border-l-2 border-primary/40 pl-3 text-sm leading-relaxed">
            {t('voice_profile.consent_statement')}
          </blockquote>
          <RecordingInputs rec={rec} disabled={busy} />
          <Button
            type="button"
            size="sm"
            disabled={busy || rec.isStarting || rec.isCleaning}
            onClick={() => (rec.isRecording ? rec.stop() : void rec.start())}
          >
            {rec.isRecording ? <SquareIcon /> : <MicIcon />}
            {t(rec.isRecording ? 'voice_profile.consent_stop' : 'voice_profile.consent_record')}
            {rec.isRecording && <span className="tabular-nums">{Math.floor(rec.seconds)}s</span>}
          </Button>
        </>
      )}
      {Boolean(profile.is_locked) && (
        <div className="space-y-2 border-t border-border/50 pt-3">
          <p className="text-sm font-medium">{t('voice_profile.locked')}</p>
          <p className="text-xs text-muted-foreground">{t('voice_profile.locked_explain')}</p>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => setConfirm('unlock')}
          >
            {t('voice_profile.unlock')}
          </Button>
        </div>
      )}
      {confirm && (
        <div className="space-y-2 rounded-md bg-muted/40 p-3 text-xs">
          <p>
            {t(
              confirm === 'unlock'
                ? 'voice_profile.unlock_confirm'
                : 'voice_profile.consent_revoke_confirm',
            )}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              disabled={disabled}
              onClick={() => void change(confirm)}
            >
              {t('common.confirm')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => setConfirm(null)}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}
      {(busy || rec.isCleaning) && (
        <p role="status" className="text-xs text-muted-foreground">
          {t('preferences.loading')}
        </p>
      )}
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {t(
            confirm === 'unlock' ? 'voice_profile.unlock_failed' : 'voice_profile.consent_failed',
            { message: failed },
          )}
        </p>
      )}
    </details>
  );
}
