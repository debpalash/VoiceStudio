import React, { useState, useRef } from 'react';
import {
  BookOpen,
  Search,
  Download,
  Ellipsis,
  Headphones,
  Play,
  Pause,
  Trash2,
  X,
  Loader,
  UserPlus,
  Upload,
  Scissors,
  Package,
} from 'lucide-react';
import { Button, Input, Menu } from '../../ui';
import { useGalleryVoices } from '../../api/hooks';
import { importPersona } from '../../api/profiles';
import {
  searchYoutube,
  downloadYoutubeClip,
  deleteGalleryVoice,
  saveVoiceAsProfile,
  uploadVoiceClip,
  previewVoiceUrl,
} from '../../api/gallery';
import AudioTrimmer from '../AudioTrimmer';
import GallerySectionHeader from './GallerySectionHeader';
import { apiFetch } from '../../api/client';
import { askConfirm } from '../../utils/dialog';

// ── My Imports zone (neutral importer) ───────────────────────────────────────
export default function ImportsZone({
  t,
  playingId,
  loadingPreviewId,
  onPlayGallery,
  onUseProfile,
  flash,
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [trimming, setTrimming] = useState(null); // { voice, file }
  const [savingProfileId, setSavingProfileId] = useState(null);
  const fileRef = useRef(null);
  const personaRef = useRef(null);
  const savingProfileRef = useRef(false);
  const [importingPersona, setImportingPersona] = useState(false);

  const voicesQ = useGalleryVoices();
  const voices = voicesQ.data || [];
  const reload = () => voicesQ.refetch();

  // Import a portable .ovsvoice (or legacy .omnivoice) persona bundle (#29).
  const handlePersonaImport = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportingPersona(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await importPersona(fd);
      reload();
      flash(
        t('gallery.persona_imported', {
          defaultValue: 'Imported "{{name}}"{{unverified}}.',
          name: res.name,
          unverified: res.verified_own_voice
            ? ''
            : t('gallery.persona_unverified_suffix', { defaultValue: ' (unverified)' }),
        }),
      );
    } catch (err) {
      const code = String(err?.message || err);
      const msg = code.includes('413')
        ? t('gallery.persona_too_large', { defaultValue: 'That bundle is too large (max 100 MB).' })
        : t('gallery.persona_import_failed', {
            defaultValue: 'Could not import that persona bundle.',
          });
      flash(msg);
    } finally {
      setImportingPersona(false);
      if (personaRef.current) personaRef.current.value = '';
    }
  };

  const isUrl = /^https?:\/\//i.test(query.trim());

  const handleSearch = async () => {
    const q = query.trim();
    if (!q) return;
    if (isUrl) {
      setIsDownloading(true);
      try {
        await downloadYoutubeClip({
          video_url: q,
          start_time: 0,
          duration: 15,
          character_name: t('gallery.imported_clip', { defaultValue: 'Imported clip' }),
          category: 'import',
          description: q,
        });
        reload();
        setQuery('');
      } catch (e) {
        flash(
          t('gallery.download_failed', {
            defaultValue: 'Download failed: {{msg}}',
            msg: e.message,
          }),
        );
      } finally {
        setIsDownloading(false);
      }
      return;
    }
    setIsSearching(true);
    try {
      const r = await searchYoutube(q, 'import', 10);
      setResults(r.results || []);
    } catch (e) {
      flash(
        t('gallery.search_failed', {
          message: e?.message || String(e),
          defaultValue: 'Search failed: {{message}}',
        }),
      );
    } finally {
      setIsSearching(false);
    }
  };

  const handleDownload = async (info) => {
    setIsDownloading(true);
    try {
      await downloadYoutubeClip({
        video_url: `https://youtube.com/watch?v=${info.video_id}`,
        start_time: 0,
        duration: Math.min(parseFloat(info.duration) || 15, 30),
        character_name: (info.title || '').substring(0, 40),
        category: 'import',
        description: info.title,
      });
      reload();
      setResults([]);
    } catch (e) {
      flash(
        t('gallery.download_failed', { defaultValue: 'Download failed: {{msg}}', msg: e.message }),
      );
    } finally {
      setIsDownloading(false);
    }
  };

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('name', file.name.replace(/\.[^.]+$/, ''));
    fd.append('category', 'import');
    fd.append('audio', file);
    try {
      await uploadVoiceClip(fd);
      reload();
    } catch (err) {
      flash(
        t('gallery.upload_failed', {
          message: err?.message || String(err),
          defaultValue: 'Upload failed: {{message}}',
        }),
      );
    } finally {
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleSaveProfile = async (v, target = 'studio') => {
    // Close the double-click window before React can paint `disabled`.
    if (savingProfileRef.current) return;
    savingProfileRef.current = true;
    setSavingProfileId(v.id);
    try {
      const profile = await saveVoiceAsProfile(v.id, v.name);
      if (onUseProfile) onUseProfile(profile, target);
      else
        flash(
          t('gallery.saved_as_profile', {
            defaultValue: 'Added "{{name}}" to your voices.',
            name: profile.name,
          }),
        );
    } catch (e) {
      flash(
        t('gallery.save_failed', {
          message: e?.message || String(e),
          defaultValue: 'Could not save profile: {{message}}',
        }),
      );
    } finally {
      savingProfileRef.current = false;
      setSavingProfileId(null);
    }
  };

  const handleDelete = async (v) => {
    if (
      !(await askConfirm(
        t('gallery.confirm_delete', { defaultValue: 'Delete "{{name}}"?', name: v.name }),
      ))
    )
      return;
    try {
      await deleteGalleryVoice(v.id);
      reload();
    } catch (e) {
      flash(
        t('gallery.delete_failed', {
          message: e?.message || String(e),
          defaultValue: 'Could not delete: {{message}}',
        }),
      );
    }
  };

  const handleTrimClick = async (v) => {
    try {
      const resp = await apiFetch(previewVoiceUrl(v.id));
      const blob = await resp.blob();
      const file = new File([blob], `${v.name}.wav`, { type: 'audio/wav' });
      setTrimming({ voice: v, file });
    } catch (e) {
      flash(
        t('gallery.trim_load_failed', {
          message: e?.message || String(e),
          defaultValue: 'Could not load audio for trimming: {{message}}',
        }),
      );
    }
  };

  const handleConfirmTrim = async (trimmedFile) => {
    if (!trimming) return;
    const { voice } = trimming;
    const fd = new FormData();
    fd.append('name', `${voice.name} (Cropped)`);
    fd.append('character', voice.character || '');
    fd.append('category', 'import');
    fd.append('description', voice.description || '');
    fd.append('audio', trimmedFile);
    try {
      await uploadVoiceClip(fd);
      reload();
      setTrimming(null);
    } catch (e) {
      flash(
        t('gallery.upload_failed', {
          message: e?.message || String(e),
          defaultValue: 'Upload failed: {{message}}',
        }),
      );
    }
  };

  const voicePlay =
    'flex items-center justify-center w-[30px] h-[30px] rounded-full border border-transparent bg-[var(--chrome-hover-bg)] text-[var(--text-primary)] cursor-pointer flex-shrink-0 transition-colors hover:bg-[var(--chrome-accent-bg)] hover:text-[var(--chrome-accent)] disabled:cursor-not-allowed disabled:opacity-50';
  const actionBtn =
    'flex items-center justify-center w-[28px] h-[28px] bg-transparent text-[var(--text-secondary)] rounded-[8px] cursor-pointer transition-colors hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--text-primary)]';
  const dangerBtn =
    'flex items-center justify-center w-[28px] h-[28px] bg-transparent text-[var(--text-secondary)] rounded-[8px] cursor-pointer transition-colors hover:bg-[color-mix(in_srgb,var(--chrome-severity-err,#cc241d)_12%,transparent)] hover:text-[color:var(--chrome-severity-err,#cc241d)]';

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <p className="m-0 mb-[8px] shrink-0 text-[0.72rem] leading-[1.5] text-[var(--text-secondary)]">
        {t('gallery.import_explainer', {
          defaultValue:
            'Paste a URL you have the rights to (or upload a file), trim the part you need, and save it as a voice. You are responsible for the licensing of anything you import.',
        })}
      </p>

      <div className="shrink-0 flex flex-col gap-[10px]">
        <div className="flex gap-[6px]">
          <Input
            className="flex-1"
            placeholder={t('gallery.import_placeholder', {
              defaultValue: 'Paste a video/audio URL, or type to search…',
            })}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSearch();
            }}
          />
          <Button onClick={handleSearch} disabled={isSearching || isDownloading} size="sm">
            {isSearching || isDownloading ? (
              <Loader size={14} className="spin" />
            ) : isUrl ? (
              <Download size={14} />
            ) : (
              <Search size={14} />
            )}
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept="audio/*,video/*"
            hidden
            aria-label={t('gallery.upload', { defaultValue: 'Upload file' })}
            onChange={handleUpload}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fileRef.current?.click()}
            title={t('gallery.upload', { defaultValue: 'Upload file' })}
          >
            <Upload size={14} />
          </Button>
          <input
            ref={personaRef}
            type="file"
            accept=".ovsvoice,.omnivoice"
            hidden
            aria-label={t('gallery.import_persona', {
              defaultValue: 'Import a .ovsvoice persona bundle',
            })}
            onChange={handlePersonaImport}
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={importingPersona}
            onClick={() => personaRef.current?.click()}
            title={t('gallery.import_persona', {
              defaultValue: 'Import a .ovsvoice persona bundle',
            })}
          >
            {importingPersona ? <Loader size={14} className="spin" /> : <Package size={14} />}
          </Button>
        </div>
      </div>

      {results.length > 0 && (
        <div className="shrink-0 rounded-[12px] bg-[color-mix(in_srgb,var(--chrome-fg)_2.5%,transparent)] max-h-[180px] overflow-hidden flex flex-col mb-[8px]">
          <div className="flex justify-between items-center px-[12px] py-[8px] text-[0.75rem] font-medium shrink-0 text-[var(--text-secondary)]">
            <span>
              {t('gallery.search_results', {
                defaultValue: '{{count}} results',
                count: results.length,
              })}
            </span>
            <button
              className="bg-transparent border border-transparent rounded-[8px] text-[var(--text-secondary)] cursor-pointer p-[4px] transition-colors hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--color-fg)]"
              onClick={() => setResults([])}
              aria-label={t('common.close', { defaultValue: 'Close' })}
            >
              <X size={14} />
            </button>
          </div>
          <div className="overflow-y-auto flex-1">
            {results.map((r, i) => (
              <div
                key={i}
                className="flex justify-between items-center px-[10px] py-[8px] gap-[8px] border-b border-transparent last:border-0"
              >
                <div className="flex-1 min-w-0 flex flex-col gap-[2px]">
                  <span className="text-[0.75rem] truncate">{r.title}</span>
                  <span className="text-[0.65rem] text-[var(--text-secondary)]">
                    {r.duration || '?'}s
                  </span>
                </div>
                <Button size="sm" onClick={() => handleDownload(r)} disabled={isDownloading}>
                  <Download size={12} /> {t('gallery.import', { defaultValue: 'Import' })}
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      <GallerySectionHeader
        icon={<Upload size={12} strokeWidth={1.5} aria-hidden="true" />}
        title={t('gallery.my_imports', { defaultValue: 'My Imports' })}
        count={voices.length}
      />

      {voicesQ.isLoading ? (
        <div className="flex items-center justify-center gap-[8px] p-[24px] text-[var(--text-secondary)]">
          <Loader className="spin" size={18} />
        </div>
      ) : voices.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-[8px] px-[16px] py-[32px] text-center text-[var(--text-secondary)]">
          <Upload size={20} strokeWidth={1.5} aria-hidden="true" />
          <span className="max-w-[300px] text-[0.78rem] leading-[1.6]">
            {t('gallery.no_imports', {
              defaultValue: 'Nothing imported yet. Paste a URL above to get started.',
            })}
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-[2px] overflow-y-auto flex-1 pr-[4px]">
          {voices.map((v) => (
            <div
              key={v.id}
              className="group flex min-h-[56px] min-w-0 items-center gap-[10px] rounded-[10px] border border-transparent bg-transparent px-[10px] py-[8px] transition-colors hover:bg-[color-mix(in_srgb,var(--chrome-fg)_4%,transparent)]"
            >
              <button
                className={`${voicePlay} disabled:cursor-not-allowed disabled:opacity-50`}
                onClick={() => onPlayGallery(v)}
                disabled={Boolean(loadingPreviewId)}
                aria-busy={loadingPreviewId === v.id}
                aria-label={t('gallery.preview', { defaultValue: 'Preview' })}
                title={t('gallery.preview', { defaultValue: 'Preview' })}
              >
                {loadingPreviewId === v.id ? (
                  <Loader className="spin" size={16} />
                ) : playingId === v.id ? (
                  <Pause size={16} />
                ) : (
                  <Play size={16} />
                )}
              </button>
              <div className="flex-1 min-w-0 flex flex-col gap-[1px]">
                <span className="text-[0.8rem] font-medium truncate">{v.name}</span>
                <span className="flex items-center gap-[3px] text-[0.65rem] text-[var(--text-secondary)]">
                  {Math.round(v.duration || 0)}s
                </span>
              </div>
              <div className="flex gap-[3px]">
                <button
                  className={actionBtn}
                  onClick={() => handleTrimClick(v)}
                  title={t('gallery.trim', { defaultValue: 'Trim' })}
                >
                  <Scissors size={14} />
                </button>
                <button
                  className={`${actionBtn} disabled:cursor-not-allowed disabled:opacity-40`}
                  onClick={() => handleSaveProfile(v)}
                  disabled={Boolean(savingProfileId)}
                  aria-busy={savingProfileId === v.id}
                  aria-label={t('gallery.use_voice', { defaultValue: 'Use voice' })}
                  title={t('gallery.use_voice', { defaultValue: 'Use voice' })}
                >
                  {savingProfileId === v.id ? (
                    <Loader className="spin" size={14} aria-hidden="true" />
                  ) : (
                    <UserPlus size={14} aria-hidden="true" />
                  )}
                </button>
                {onUseProfile ? (
                  <Menu
                    placement="bottom-end"
                    disabled={Boolean(savingProfileId)}
                    items={[
                      {
                        id: 'stories',
                        icon: BookOpen,
                        label: t('gallery.use_in_stories', { defaultValue: 'Use in Stories' }),
                        onSelect: () => handleSaveProfile(v, 'stories'),
                      },
                      {
                        id: 'audiobook',
                        icon: Headphones,
                        label: t('gallery.set_audiobook_default', {
                          defaultValue: 'Set as Audiobook default',
                        }),
                        onSelect: () => handleSaveProfile(v, 'audiobook'),
                      },
                    ]}
                  >
                    <button
                      className={`${actionBtn} disabled:cursor-not-allowed disabled:opacity-40`}
                      disabled={Boolean(savingProfileId)}
                      aria-label={t('gallery.more_actions', { defaultValue: 'More actions' })}
                      title={t('gallery.more_actions', { defaultValue: 'More actions' })}
                    >
                      <Ellipsis size={14} aria-hidden="true" />
                    </button>
                  </Menu>
                ) : null}
                <button
                  className={dangerBtn}
                  onClick={() => handleDelete(v)}
                  title={t('gallery.delete', { defaultValue: 'Delete' })}
                  aria-label={t('gallery.delete', { defaultValue: 'Delete' })}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {trimming && (
        <AudioTrimmer
          file={trimming.file}
          maxSeconds={60}
          onConfirm={handleConfirmTrim}
          onCancel={() => setTrimming(null)}
        />
      )}
    </div>
  );
}
