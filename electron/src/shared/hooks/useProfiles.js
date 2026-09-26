import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store';
import {
  createProfile,
  deleteProfile as apiDeleteProfile,
  lockProfile,
  unlockProfile,
} from '../api/profiles';
import { generateSpeech, audioUrlWithCacheBust } from '../api/generate';
import { apiFetch } from '../api/client';
import { playBlobAudio } from '../utils/media';
import { PRESETS } from '../utils/constants';
import {
  instructToFormValue,
  instructToVdStates,
  mergeDescribedAttrs,
  buildDesignInstruct,
} from '../utils/voiceInstruct';
import { askConfirm } from '../utils/dialog';
import { toast } from 'react-hot-toast';
import { recordValueMoment } from '../utils/donationMoments';

/**
 * Encapsulates voice-profile CRUD, lock/unlock, preview, and save-from-history.
 */
export default function useProfiles({ loadHistory, loadProfiles }) {
  const { t } = useTranslation();
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [showSaveProfile, setShowSaveProfile] = useState(false);
  const [profileName, setProfileName] = useState('');
  const [previewLoading, setPreviewLoading] = useState(null);
  const [segmentPreviewLoading, setSegmentPreviewLoading] = useState(null);

  // Voice Preview floating card
  const [isVoicePreviewOpen, setIsVoicePreviewOpen] = useState(false);
  const [voicePreviewProfileId, setVoicePreviewProfileId] = useState('');

  const setRefText = useAppStore((s) => s.setRefText);
  const setInstruct = useAppStore((s) => s.setInstruct);
  const setLanguage = useAppStore((s) => s.setLanguage);
  const setVdStates = useAppStore((s) => s.setVdStates);
  const setDefineMethod = useAppStore((s) => s.setDefineMethod);
  const language = useAppStore((s) => s.language);
  const mode = useAppStore((s) => s.mode);
  const steps = useAppStore((s) => s.steps);
  const cfg = useAppStore((s) => s.cfg);
  const dubLang = useAppStore((s) => s.dubLang);
  const dubSegments = useAppStore((s) => s.dubSegments);
  const text = useAppStore((s) => s.text);

  // loadProfiles is provided by useAppData (single source of truth)

  const handleSaveProfile = useCallback(
    async (refAudio, refText, instruct, language) => {
      if (!profileName.trim() || !refAudio) return toast.error(t('profiles.need_name_audio'));
      const formData = new FormData();
      formData.append('name', profileName);
      const arrBuf = await refAudio.arrayBuffer();
      const safeBlob = new Blob([arrBuf], { type: refAudio.type });
      formData.append('ref_audio', safeBlob, refAudio.name || 'profile.wav');
      formData.append('ref_text', refText);
      // #1010: the backend only sanitizes instruct on save for kind='design'
      // profiles — a clone profile (this call always creates kind='clone')
      // would silently persist an unsupported free-text instruct and then
      // 400 every single time it's used to generate. Filter here too.
      const { instruct: safeInst } = buildDesignInstruct({}, instruct);
      formData.append('instruct', safeInst);
      formData.append('language', language);
      try {
        await createProfile(formData);
        setShowSaveProfile(false);
        setProfileName('');
        await loadProfiles();
        // Success-only donation moment — a saved voice clone is a real
        // deliverable. Never fires on the error branch below.
        recordValueMoment('clone');
      } catch (e) {
        toast.error(e.message);
      }
    },
    [profileName, loadProfiles, t],
  );

  const handleDeleteProfile = useCallback(
    async (id) => {
      if (!(await askConfirm('Delete this voice profile?'))) return;
      await apiDeleteProfile(id);
      if (selectedProfile === id) setSelectedProfile(null);
      await loadProfiles();
    },
    [selectedProfile, loadProfiles],
  );

  const handleSelectProfile = useCallback(
    (profile) => {
      setSelectedProfile(profile.id);
      setRefText(profile.ref_text || '');
      // Profile selection replaces the whole voice context. An Auto-language
      // profile must reset an explicit language left by the previous voice.
      setLanguage(profile.language || 'Auto');
      // The profile's kind picks the "Define voice" method implicitly: design
      // profiles open the design controls, everything else the audio path.
      setDefineMethod(profile.kind === 'design' ? 'design' : 'audio');
      // Design profiles (0005) carry their category picks — restore the sliders
      // so selecting one makes it re-editable, not just re-usable.
      if (profile.kind === 'design') {
        // Always replace the prior recipe. Older/imported design profiles may
        // have no vd_states (or malformed JSON); their validator-safe instruct
        // still reconstructs the controls, and an empty recipe resets every
        // category to Auto instead of leaking the previously selected voice.
        let next = instructToVdStates(profile.instruct || '');
        if (profile.vd_states) {
          try {
            const parsed = JSON.parse(profile.vd_states);
            // #983: a profile saved by an older/foreign client (or hand-edited)
            // can carry a partial shape — mergeDescribedAttrs guarantees every
            // CATEGORIES key is present and validates every value.
            if (parsed && typeof parsed === 'object') {
              next = mergeDescribedAttrs({ ...next, ...parsed });
            }
          } catch {
            /* instruct-derived fallback above is already complete */
          }
        }
        setVdStates(next);
        // The profile recipe already lives in the sliders. Mirroring it into
        // free text makes buildDesignInstruct report every token as a duplicate
        // and can resurrect stale prose from older profiles.
        setInstruct('');
      } else {
        setInstruct(profile.instruct || '');
      }
    },
    [setRefText, setInstruct, setLanguage, setVdStates, setDefineMethod],
  );

  /** Save the current design (vd_states + instruct) as a reusable profile.
      The backend renders a deterministic identity sample (seed 42). */
  const handleSaveDesignProfile = useCallback(
    async (vdStates, instruct, language) => {
      if (!profileName.trim()) return toast.error('Need a profile name');
      const fd = new FormData();
      fd.append('name', profileName);
      fd.append('kind', 'design');
      fd.append('vd_states', JSON.stringify(vdStates || {}));
      // Defensive: instruct must be the STRING. buildDesignInstruct() returns an
      // object — appending it coerced to "[object Object]", poisoning the profile
      // (#550 et al). instructToFormValue extracts .instruct if an object slips
      // through, so the field is never garbage.
      fd.append('instruct', instructToFormValue(instruct));
      fd.append('language', language || 'Auto');
      try {
        await createProfile(fd);
        setShowSaveProfile(false);
        setProfileName('');
        await loadProfiles();
        toast.success('Design saved as a voice profile');
      } catch (e) {
        toast.error(e.message);
      }
    },
    [profileName, loadProfiles],
  );

  const handlePreviewVoice = useCallback(
    async (proj, e) => {
      e.stopPropagation();
      if (previewLoading) return;

      let previewText = 'This is a voice preview.';
      let reqLang = language;

      if (mode === 'dub' && dubSegments.length > 0) {
        let seg = dubSegments.find((s) => s.profile_id === proj.id && s.text.trim().length > 0);
        if (!seg) seg = dubSegments.find((s) => s.text.trim().length > 0);
        if (seg) previewText = seg.text;
        reqLang = dubLang;
      } else if (text.trim() !== '') {
        previewText = text;
      }

      setPreviewLoading(proj.id);
      const toastId = toast.loading(t('profiles.synthesizing_preview', { name: proj.name }));

      try {
        const formData = new FormData();
        formData.append('text', previewText);
        formData.append('profile_id', proj.id);
        if (reqLang && reqLang !== 'Auto') formData.append('language', reqLang);
        formData.append('num_step', steps || 16);
        const res = await generateSpeech(formData);
        const blob = await res.blob();
        toast.success(t('profiles.preview_ready'), { id: toastId });
        playBlobAudio(blob, { label: proj.name }).catch(() =>
          toast.error(t('profiles.playback_failed'), { id: toastId }),
        );
        await loadHistory();
      } catch (err) {
        const message =
          err?.code === 'tts_generation_busy'
            ? t('tts_errors.generation_in_progress')
            : t('profiles.preview_failed', { message: err.message });
        toast.error(message, { id: toastId });
      } finally {
        setPreviewLoading(null);
      }
    },
    [previewLoading, language, mode, dubSegments, dubLang, text, steps, loadHistory, t],
  );

  const handleSegmentPreview = useCallback(
    async (seg, e) => {
      e.preventDefault();
      if (segmentPreviewLoading) return;
      setSegmentPreviewLoading(seg.id);
      const toastId = toast.loading(t('profiles.synthesizing_segment'));

      try {
        const formData = new FormData();
        formData.append('text', seg.text);

        let fin_prof = seg.profile_id || '';
        let fin_inst = seg.instruct || '';

        if (fin_prof.startsWith('preset:')) {
          const pr = PRESETS.find((p) => p.id === fin_prof.replace('preset:', ''));
          if (pr) {
            const parts = Object.values(pr.attrs).filter((v) => v !== 'Auto');
            if (fin_inst.trim()) parts.push(fin_inst.trim());
            fin_inst = parts.join(', ');
          }
          fin_prof = '';
        }

        // #1010: this instruct string comes straight from segment/preset data,
        // never through the validator-safe builder — a preset's raw attrs or a
        // free-text style field can carry phrases outside the active engine's
        // supported instruct vocabulary, 400ing instead of previewing. Same
        // client-side guard useTTS.js already applies to the clone path.
        if (fin_inst) {
          const { instruct: safeInst, unsupported, duplicates } = buildDesignInstruct({}, fin_inst);
          if (unsupported.length) {
            toast(t('tts_errors.ignored_unsupported', { items: unsupported.join(', ') }), {
              icon: '⚠️',
            });
          }
          if (duplicates.length) {
            toast(t('tts_errors.ignored_duplicate', { items: duplicates.join(', ') }), {
              icon: '⚠️',
            });
          }
          fin_inst = safeInst;
        }
        if (fin_prof) formData.append('profile_id', fin_prof);
        if (fin_inst) formData.append('instruct', fin_inst);
        const fin_lang = seg.target_lang || dubLang;
        if (fin_lang !== 'Auto') formData.append('language', fin_lang);

        formData.append('num_step', 8);
        formData.append('guidance_scale', cfg || 2.0);
        if (seg.speed && seg.speed !== 1.0) formData.append('speed', seg.speed);

        const res = await generateSpeech(formData);
        const blob = await res.blob();
        toast.success(t('profiles.preview_ready'), { id: toastId });
        // The dub segment has no display name — its (translated) line text is
        // the most recognisable label for the global player.
        playBlobAudio(blob, { label: seg.text }).catch(() =>
          toast.error(t('profiles.playback_failed'), { id: toastId }),
        );
      } catch (err) {
        const message =
          err?.code === 'tts_generation_busy'
            ? t('tts_errors.generation_in_progress')
            : t('profiles.preview_failed', { message: err.message });
        toast.error(message, { id: toastId });
      } finally {
        setSegmentPreviewLoading(null);
      }
    },
    [segmentPreviewLoading, dubLang, cfg, t],
  );

  const handleSaveHistoryAsProfile = useCallback(
    async (item) => {
      try {
        const pName = `Voice ${new Date().toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' })} — ${(item.mode || 'design').toUpperCase()}`;
        const response = await apiFetch(audioUrlWithCacheBust(item.audio_path));
        const blob = await response.blob();
        const file = new File([blob], item.audio_path, { type: 'audio/wav' });

        const formData = new FormData();
        formData.append('name', pName);
        formData.append('ref_audio', file);
        const extractedText = item.text
          ? item.text.length > 50
            ? item.text.substring(0, 50)
            : item.text
          : '';
        formData.append('ref_text', extractedText);
        // #1010: same guard as handleSaveProfile — this always creates a
        // kind='clone' profile, which the backend never sanitizes on save.
        const { instruct: safeHistInst } = buildDesignInstruct({}, item.instruct || '');
        formData.append('instruct', safeHistInst);
        formData.append('language', item.language || 'Auto');
        if (item.seed !== undefined && item.seed !== null) {
          formData.append('seed', item.seed);
        }

        await createProfile(formData);
        toast.success(t('profiles.saved'));
        await loadProfiles();
      } catch (e) {
        toast.error(e.message || t('profiles.save_failed'));
      }
    },
    [loadProfiles, t],
  );

  const handleLockProfile = useCallback(
    async (profileId, historyId, seed) => {
      try {
        const formData = new FormData();
        formData.append('history_id', historyId);
        if (seed !== null && seed !== undefined) formData.append('seed', seed);
        await lockProfile(profileId, formData);
        toast.success(t('profiles.locked'));
        await loadProfiles();
      } catch (e) {
        toast.error(e.message || t('profiles.lock_failed'));
      }
    },
    [loadProfiles, t],
  );

  const handleUnlockProfile = useCallback(
    async (profileId) => {
      try {
        await unlockProfile(profileId);
        toast.success(t('profiles.unlocked'));
        await loadProfiles();
      } catch (e) {
        toast.error(e.message || t('profiles.unlock_failed'));
      }
    },
    [loadProfiles, t],
  );

  return {
    selectedProfile,
    setSelectedProfile,
    showSaveProfile,
    setShowSaveProfile,
    profileName,
    setProfileName,
    previewLoading,
    segmentPreviewLoading,
    isVoicePreviewOpen,
    setIsVoicePreviewOpen,
    voicePreviewProfileId,
    setVoicePreviewProfileId,
    handleSaveProfile,
    handleSaveDesignProfile,
    handleDeleteProfile,
    handleSelectProfile,
    handlePreviewVoice,
    handleSegmentPreview,
    handleSaveHistoryAsProfile,
    handleLockProfile,
    handleUnlockProfile,
  };
}
