import React, { useState, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Upload, Film, Globe, X, Plus } from 'lucide-react';
import { Button, Dialog, Select } from '../ui';
import MultiLangPicker from './MultiLangPicker';
import { PRESETS } from '../utils/constants';

/**
 * BatchAddDialog — multi-file drop zone + shared settings for batch dubbing.
 *
 * Users drop N video files, pick languages + voice, then click "Add to Queue".
 * Each file is POSTed as a separate job to the batch endpoint.
 */
export default function BatchAddDialog({
  open,
  onClose,
  profiles = [],
  onEnqueue, // async (files, settings) => void
}) {
  const { t } = useTranslation();
  const [files, setFiles] = useState([]);
  const [langs, setLangs] = useState([{ lang: 'Spanish', code: 'es' }]);
  const [voiceId, setVoiceId] = useState('');
  const [preserveBg, setPreserveBg] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const dropped = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('video/'));
    if (dropped.length) setFiles((prev) => [...prev, ...dropped]);
  }, []);

  const removeFile = (idx) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    if (!files.length || !langs.length) return;
    setSubmitting(true);
    try {
      await onEnqueue?.(files, { langs, voiceId, preserveBg });
      setFiles([]);
      onClose?.();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="md"
      title={
        <span className="inline-flex items-center gap-[6px]">
          <Plus size={14} /> {t('batch.add_to_queue_title')}
        </span>
      }
      footer={
        <>
          <span className="flex-1 font-mono text-[0.68rem] text-[var(--chrome-fg-dim)]">
            {files.length > 0 && langs.length > 0
              ? t('batch.estimate', {
                  videos: files.length,
                  langs: langs.length,
                  jobs: files.length * langs.length,
                })
              : t('batch.select_files_langs')}
          </span>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            disabled={!files.length || !langs.length || submitting}
            loading={submitting}
            leading={!submitting && <Plus size={10} />}
          >
            {t('batch.add_to_queue')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-[14px]">
        {/* Drop zone */}
        <div
          className={`flex cursor-pointer flex-col items-center justify-center gap-[6px] rounded-[10px] border-2 border-dashed px-4 py-7 text-[0.82rem] transition-all hover:border-[var(--chrome-accent)] hover:bg-white/[0.02] hover:text-[var(--chrome-fg)] ${
            dragOver
              ? 'border-[var(--chrome-accent)] bg-white/[0.02] text-[var(--chrome-fg)]'
              : 'border-transparent text-[var(--chrome-fg-muted)]'
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            setDragOver(false);
            handleDrop(e);
          }}
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload size={24} />
          <span>{t('batch.drop_hint_text')}</span>
          <span className="font-mono text-[0.65rem] text-[var(--chrome-fg-dim)]">
            {t('batch.drop_formats')}
          </span>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="video/*"
          multiple
          className="hidden"
          aria-label={t('batch.file_input_label')}
          onChange={(e) => {
            const added = Array.from(e.target.files);
            if (added.length) setFiles((prev) => [...prev, ...added]);
            e.target.value = '';
          }}
        />

        {/* File list */}
        {files.length > 0 && (
          <div className="flex flex-col gap-[4px]">
            <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
              {t('batch.files_kicker', { count: files.length })}
            </span>
            {files.map((f, i) => (
              <div
                key={`${f.name}-${i}`}
                className="flex items-center gap-[6px] rounded-[6px] bg-[var(--chrome-hover-bg)] p-[4px_8px] text-[0.76rem]"
              >
                <Film size={10} />
                <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-[var(--chrome-fg)]">
                  {f.name}
                </span>
                <span className="shrink-0 font-mono text-[0.68rem] text-[var(--chrome-fg-dim)]">
                  {t('batch.file_size_mb', { size: (f.size / 1024 / 1024).toFixed(1) })}
                </span>
                <button
                  type="button"
                  className="cursor-pointer rounded-[4px] border-0 bg-transparent p-[2px] text-[var(--chrome-fg-dim)] hover:text-[var(--color-danger)]"
                  onClick={() => removeFile(i)}
                >
                  <X size={9} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Settings */}
        <div className="flex flex-col gap-[12px]">
          <div className="flex flex-col gap-[6px]">
            <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
              <Globe size={9} /> {t('batch.target_languages')}
            </span>
            <MultiLangPicker selected={langs} onChange={setLangs} />
          </div>

          <div className="flex flex-col gap-[6px]">
            <span className="mb-[4px] flex items-center gap-[4px] font-mono text-[0.62rem] font-semibold uppercase tracking-[0.04em] text-[var(--chrome-fg-dim)]">
              {t('batch.voice_kicker')}
            </span>
            <Select size="sm" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
              <option value="">{t('batch.default_option')}</option>
              {profiles.filter((p) => !p.instruct).length > 0 && (
                <optgroup label={t('batch.clone_profiles')}>
                  {profiles
                    .filter((p) => !p.instruct)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </optgroup>
              )}
              {PRESETS.length > 0 && (
                <optgroup label={t('batch.presets')}>
                  {PRESETS.map((p) => (
                    <option key={p.id} value={`preset:${p.id}`}>
                      {p.name}
                    </option>
                  ))}
                </optgroup>
              )}
            </Select>
          </div>

          <label className="flex cursor-pointer items-center gap-2 text-[0.78rem] text-[var(--chrome-fg)]">
            <input
              type="checkbox"
              className="cursor-pointer"
              checked={preserveBg}
              onChange={(e) => setPreserveBg(e.target.checked)}
            />
            <span>{t('batch.preserve_bg')}</span>
          </label>
        </div>
      </div>
    </Dialog>
  );
}
