import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import { toastErrorWithReport } from '../utils/errorToast';
import { ArrowLeft, Fingerprint, Wand2, Sparkles } from 'lucide-react';
import { Button } from '../ui';
import {
  getProfile,
  getProfileUsage,
  updateProfile,
  deleteProfile,
  unlockProfile,
  recordConsent,
  revokeConsent,
  exportPersona,
} from '../api/profiles';
import useRecording from '../hooks/useRecording';
import { generateSpeech } from '../api/generate';
import { API } from '../api/client';
import { useAppStore } from '../store';
import ProfileHeader from '../components/profile/ProfileHeader';
import ProfileDetails from '../components/profile/ProfileDetails';
import ProfileActivity from '../components/profile/ProfileActivity';
import { askConfirm } from '../utils/dialog';

/**
 * VoiceProfile — per-voice detail page.
 *
 * Route (via App mode):
 *   mode === 'voice' && activeVoiceId set.
 *
 * Props:
 *   voiceId       string
 *   onBack()      return to previous mode
 *   onOpenProject(id)  navigate to a dub project (from usage list)
 *   onDeleted()   called after successful delete
 */
export default function VoiceProfile({ voiceId, onBack, onOpenProject, onDeleted }) {
  const { t } = useTranslation();
  const autoPlayPreview = useAppStore((s) => s.autoPlayPreview);
  const [profile, setProfile] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);

  // Try-it panel
  const [testText, setTestText] = useState(t('voice_profile.test_text'));
  const [testGenerating, setTestGenerating] = useState(false);
  const [testAudioUrl, setTestAudioUrl] = useState(null);

  // Consent lock (Wave 0.2): record a spoken consent statement to mark the
  // profile as the owner's own voice. Agentic features and gallery sharing
  // gate on this flag; local synthesis never does.
  const [consentSubmitting, setConsentSubmitting] = useState(false);
  const consentStatement = t('voice_profile.consent_statement');
  const submitConsent = async (audioFile) => {
    setConsentSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('consent_audio', audioFile);
      fd.append('consent_text', consentStatement);
      await recordConsent(voiceId, fd);
      toast.success(t('voice_profile.consent_saved'));
      await reload();
    } catch (e) {
      toastErrorWithReport(t('voice_profile.consent_failed', { message: e.message }), e);
    } finally {
      setConsentSubmitting(false);
    }
  };
  const consentRec = useRecording(submitConsent);
  const onRevokeConsent = async () => {
    if (!(await askConfirm(t('voice_profile.consent_revoke_confirm')))) return;
    try {
      await revokeConsent(voiceId);
      toast.success(t('voice_profile.consent_revoked'));
      await reload();
    } catch (e) {
      toastErrorWithReport(e.message, e);
    }
  };

  // Export this profile as a portable .ovsvoice persona bundle (#29). Default
  // ships the raw reference clip; the privacy toggle (default ON) strips it so
  // only the watermarked preview travels.
  const [exporting, setExporting] = useState(false);
  const [includeReference, setIncludeReference] = useState(true);
  const onExportPersona = async () => {
    setExporting(true);
    const loadingId = toast.loading(
      t('voice_profile.persona_exporting', { defaultValue: 'Building persona…' }),
    );
    try {
      const blob = await exportPersona(voiceId, { include_reference: includeReference });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const safe =
        (profile?.name || 'persona')
          .replace(/[^a-zA-Z0-9-_ ]/g, '')
          .trim()
          .replace(/ /g, '_') || 'persona';
      a.href = url;
      a.download = `${safe}.ovsvoice`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(t('voice_profile.persona_exported', { defaultValue: 'Persona exported' }), {
        id: loadingId,
      });
    } catch (e) {
      toast.dismiss(loadingId);
      const msg =
        String(e?.message || e) === '503'
          ? t('voice_profile.persona_export_no_audio', {
              defaultValue: 'This voice has no readable audio to build a preview from.',
            })
          : t('voice_profile.persona_export_failed', { defaultValue: 'Export failed.' });
      toastErrorWithReport(msg, e);
    } finally {
      setExporting(false);
    }
  };

  const reload = useCallback(async () => {
    if (!voiceId) return;
    setLoading(true);
    try {
      const [p, u] = await Promise.all([getProfile(voiceId), getProfileUsage(voiceId)]);
      setProfile(p);
      setUsage(u);
      setDraft({
        name: p.name || '',
        instruct: p.instruct || '',
        language: p.language || 'Auto',
        ref_text: p.ref_text || '',
      });
    } catch (e) {
      toast.error(e.message || 'Failed to load voice');
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, [voiceId]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(
    () => () => {
      // Clean up any blob URL when the page unmounts.
      if (testAudioUrl && testAudioUrl.startsWith('blob:')) URL.revokeObjectURL(testAudioUrl);
    },
    [testAudioUrl],
  );

  const saveEdits = async () => {
    if (!draft.name.trim()) {
      toast.error(t('voice_profile.needs_name'));
      return;
    }
    setSaving(true);
    try {
      const next = await updateProfile(voiceId, draft);
      setProfile(next);
      setEditing(false);
      toast.success(t('voice_profile.saved'));
    } catch (e) {
      toastErrorWithReport(t('voice_profile.save_failed', { message: e.message }), e);
    } finally {
      setSaving(false);
    }
  };

  const cancelEdits = () => {
    setDraft({
      name: profile.name || '',
      instruct: profile.instruct || '',
      language: profile.language || 'Auto',
      ref_text: profile.ref_text || '',
    });
    setEditing(false);
  };

  const onDelete = async () => {
    if (!(await askConfirm(t('voice_profile.delete_confirm', { name: profile.name })))) return;
    try {
      await deleteProfile(voiceId);
      toast.success(t('voice_profile.deleted'));
      onDeleted?.();
    } catch (e) {
      toastErrorWithReport(t('voice_profile.delete_failed', { message: e.message }), e);
    }
  };

  const onUnlock = async () => {
    if (!(await askConfirm(t('voice_profile.unlock_confirm')))) return;
    try {
      await unlockProfile(voiceId);
      await reload();
      toast.success(t('voice_profile.unlocked'));
    } catch (e) {
      toast.error(t('voice_profile.unlock_failed', { message: e.message }));
    }
  };

  const runTest = async () => {
    if (!testText.trim()) return;
    setTestGenerating(true);
    try {
      const fd = new FormData();
      fd.append('text', testText);
      fd.append('profile_id', voiceId);
      if (profile.instruct) fd.append('instruct', profile.instruct);
      fd.append('num_step', 16);
      fd.append('guidance_scale', 2.0);
      fd.append('speed', 1.0);
      fd.append('denoise', true);
      fd.append('postprocess_output', true);
      const res = await generateSpeech(fd);
      const blob = await res.blob();
      if (testAudioUrl && testAudioUrl.startsWith('blob:')) URL.revokeObjectURL(testAudioUrl);
      const url = URL.createObjectURL(blob);
      setTestAudioUrl(url);
      // Playback (and autoplay) is handled by the shared WaveformPlayer below.
    } catch (e) {
      if (e?.code === 'tts_generation_busy') {
        toast.error(t('tts_errors.generation_in_progress'));
      } else {
        toastErrorWithReport(t('voice_profile.gen_failed', { message: e.message }), e);
      }
    } finally {
      setTestGenerating(false);
    }
  };

  if (loading && !profile) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-[var(--space-4)] px-[var(--space-6)] py-[var(--space-5)] text-fg-muted [font-size:var(--text-md)]">
        <Sparkles className="animate-spin text-brand" size={24} />
        <span>{t('common.loading')}</span>
      </div>
    );
  }
  if (!profile) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-[var(--space-4)] px-[var(--space-6)] py-[var(--space-5)] text-fg-muted [font-size:var(--text-md)]">
        <p>{t('voice_profile.not_found')}</p>
        <Button variant="subtle" onClick={onBack} leading={<ArrowLeft size={12} />}>
          {t('common.back')}
        </Button>
      </div>
    );
  }

  const isDesign = !!profile.instruct && !profile.ref_audio_path;
  const TypeIcon = isDesign ? Wand2 : Fingerprint;
  const createdDate = profile.created_at
    ? new Date(profile.created_at * 1000).toLocaleString()
    : '—';
  const audioUrl = `${API}/profiles/${voiceId}/audio?t=${profile.is_locked ? 'locked' : 'ref'}`;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[var(--space-5)] overflow-y-auto px-[var(--space-6)] py-[var(--space-5)] [container-type:inline-size] [container-name:voice-profile]">
      <ProfileHeader
        profile={profile}
        isDesign={isDesign}
        TypeIcon={TypeIcon}
        onBack={onBack}
        editing={editing}
        setEditing={setEditing}
        includeReference={includeReference}
        setIncludeReference={setIncludeReference}
        onExportPersona={onExportPersona}
        exporting={exporting}
        onDelete={onDelete}
        draft={draft}
        setDraft={setDraft}
        createdDate={createdDate}
        audioUrl={audioUrl}
        t={t}
      />
      <div className="grid items-start gap-[var(--space-6)] @min-[980px]/voice-profile:grid-cols-[minmax(0,1.08fr)_minmax(320px,0.92fr)]">
        <ProfileDetails
          profile={profile}
          editing={editing}
          draft={draft}
          setDraft={setDraft}
          saving={saving}
          cancelEdits={cancelEdits}
          saveEdits={saveEdits}
          onUnlock={onUnlock}
          onRevokeConsent={onRevokeConsent}
          consentStatement={consentStatement}
          consentRec={consentRec}
          consentSubmitting={consentSubmitting}
          t={t}
        />
        <ProfileActivity
          t={t}
          testText={testText}
          setTestText={setTestText}
          testGenerating={testGenerating}
          runTest={runTest}
          testAudioUrl={testAudioUrl}
          autoPlayPreview={autoPlayPreview}
          usage={usage}
          onOpenProject={onOpenProject}
        />
      </div>
    </div>
  );
}
