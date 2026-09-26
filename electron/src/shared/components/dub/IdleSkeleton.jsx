import {
  Film,
  Save,
  RotateCcw,
  AlertCircle,
  Sparkles,
  FileText,
  Loader,
  Users,
  UploadCloud,
  Link2,
  Globe,
  ChevronUp,
  ChevronDown,
  UserSquare2,
  Languages,
  Trash2,
  Play,
  Download,
  Activity,
} from 'lucide-react';
import { Button, Progress } from '../../ui';
import { useEffect, useRef } from 'react';
import WaveformTimeline from '../WaveformTimeline';
import SearchableSelect from '../SearchableSelect';
import DubbingDemo from '../DubbingDemo';
import DubFailureNotice from './DubFailureNotice';
import PrepOverlay from './PrepOverlay';
import TranscribeOverlay from './TranscribeOverlay';
import { LANG_CODES, languageLabel } from '../../utils/languages';

const SPEAKERS_INPUT =
  'w-[52px] ml-[4px] px-[6px] py-[4px] rounded-[6px] border border-[var(--border,#3c3836)] bg-[var(--input-bg,#282828)] text-inherit text-[12px]';

function AsrInstallStatus({ t, install, onAbort }) {
  const pct = typeof install?.percent === 'number' ? Math.round(install.percent) : null;
  return (
    <div
      className="flex flex-col items-center gap-[var(--space-5)] w-full"
      role="status"
      aria-live="polite"
    >
      <Loader className="spinner" size={20} color="#d3869b" aria-hidden="true" />
      <span className="text-fg font-medium text-[var(--text-lg)]">
        {t('dub.install_progress', { engine: install?.label })}
      </span>
      <div className="w-[80%] max-w-[340px]">
        <Progress value={pct} tone="brand" size="sm" />
      </div>
      {pct != null && (
        <span className="text-[var(--text-sm)] text-fg-muted [font-variant-numeric:tabular-nums]">
          {pct}%
        </span>
      )}
      <Button variant="danger" size="sm" onClick={onAbort}>
        {t('dub.prep_stop')}
      </Button>
    </div>
  );
}

export default function IdleSkeleton({
  t,
  uiLocale,
  dubVideoFile,
  activeProjectName,
  dubFilename,
  dubError,
  dubJobId,
  dubStep,
  dubFailure,
  asrInstall,
  handleInstallMissingAsr,
  handleDubRetryTranscribe,
  handleDubImportSrt,
  dubLocalBlobUrl,
  dubPrepStage,
  dubPrepProgress,
  handleDubAbort,
  transcribeElapsed,
  transcribeProgress,
  dubDuration,
  dubNumSpeakers,
  setDubNumSpeakers,
  handleDubUpload,
  demoDismissed,
  dismissDubDemo,
  setDubVideoFile,
  setDubInputType,
  setDubStep,
  fileToMediaUrl,
  setDubLocalBlobUrl,
  ingestUrl,
  setIngestUrl,
  onIngestUrl,
  fetchYtSubs,
  setFetchYtSubs,
  youtubeCookieFile,
  setYoutubeCookieFile,
  dubLangCode,
  dubSourceLangCode,
  setDubSourceLangCode,
  setDubLangCode,
  setDubLang,
  landingAdvOpen,
  setLandingAdvOpen,
  dubInstruct,
  setDubInstruct,
  onOpenQueue,
}) {
  const youtubeCookieInputRef = useRef(null);
  const videoUploadInputRef = useRef(null);
  useEffect(() => {
    if (!youtubeCookieFile && youtubeCookieInputRef.current) {
      youtubeCookieInputRef.current.value = '';
    }
  }, [youtubeCookieFile]);

  const languageOptions = LANG_CODES.map((language) => ({
    value: language.code,
    label: `${languageLabel(language.code, uiLocale, language.label)} — ${language.code}`,
  }));
  const sourceOptions = [{ value: 'auto', label: t('bootstrap.auto_detect') }, ...languageOptions];

  const isPristineIdle = !dubVideoFile && dubStep === 'idle' && !dubJobId;

  return (
    <div className={`flex-1 flex flex-col min-h-0${isPristineIdle ? ' dub-start-screen' : ''}`}>
      {/* Selected source details appear only after choosing media. */}
      {dubVideoFile && (
        <div className="flex justify-between items-center px-[12px] py-[5px] shrink-0 bg-[rgba(255,255,255,0.015)] [border:1px_solid_rgba(255,255,255,0.04)] rounded-md mb-[2px]">
          <div className="label-row dub-head__title">
            <Film className="label-icon" size={11} />
            <span className="font-semibold text-[0.85rem] overflow-hidden text-ellipsis whitespace-nowrap text-fg">
              {dubVideoFile.name}
            </span>
            {dubVideoFile && (
              <span className="text-fg-muted font-normal whitespace-nowrap text-[0.72rem]">
                · {(dubVideoFile.size / 1024 / 1024).toFixed(1)} MB
              </span>
            )}
            {activeProjectName && activeProjectName !== dubFilename && (
              <span className="text-[#b8bb26] ml-[var(--space-3)] whitespace-nowrap text-[0.72rem]">
                — {activeProjectName}
              </span>
            )}
          </div>
          {!isPristineIdle && (
            <div className="flex gap-[var(--space-2)] items-center shrink-0">
              <Button
                variant="subtle"
                size="sm"
                disabled
                title={t('dub.save')}
                aria-label={t('dub.save')}
              >
                <Save size={12} />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled
                title={t('dub.reset')}
                aria-label={t('dub.reset')}
              >
                <RotateCcw size={12} />
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Transcription failure banner — shown in the idle state when a
              job exists but transcription produced zero segments (or threw).
              Surfaces the backend error detail and offers one-click retry,
              which re-runs the ASR stream on the same job without re-uploading. */}
      {dubError && dubJobId && dubStep === 'idle' && (
        <div
          className="mb-[var(--space-2)] flex flex-wrap items-center gap-[var(--space-2)] rounded-md border border-transparent bg-[rgba(251,73,52,0.08)] px-[12px] py-[9px]"
          role="alert"
        >
          <AlertCircle size={15} className="shrink-0 text-danger" aria-hidden="true" />
          <span className="min-w-0 flex-1 text-[var(--text-sm)] text-fg break-words">
            {dubError}
          </span>
          <DubFailureNotice failure={dubFailure} />
          {asrInstall?.phase === 'missing' && asrInstall.repoId && (
            <Button variant="primary" size="sm" onClick={handleInstallMissingAsr}>
              {t('asr_missing.download', {
                label: asrInstall.label,
                size: asrInstall.sizeGb,
              })}
            </Button>
          )}
          {handleDubRetryTranscribe && (
            <Button
              variant="subtle"
              size="sm"
              onClick={handleDubRetryTranscribe}
              leading={<Sparkles size={10} />}
            >
              {t('dub.retry_transcription')}
            </Button>
          )}
          {handleDubImportSrt && (
            <label
              htmlFor="srt-import-banner-input"
              className="flex items-center gap-[6px] px-[12px] py-[6px] bg-[rgba(255,255,255,0.05)] [border:1px_solid_rgba(255,255,255,0.1)] rounded-[6px] cursor-pointer text-[0.8rem] text-fg-muted"
              title={t('dub.import_srt')}
              style={{ cursor: 'pointer' }}
            >
              <FileText size={11} /> {t('dub.import_srt_alt')}
              <input
                id="srt-import-banner-input"
                type="file"
                accept=".srt,text/srt,text/plain"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleDubImportSrt(f);
                  e.target.value = '';
                }}
              />
            </label>
          )}
        </div>
      )}

      {/* SPLIT LAYOUT skeleton */}
      <div
        className={
          dubVideoFile
            ? 'grid grid-cols-2 max-[1000px]:grid-cols-1 max-[1000px]:grid-rows-[auto_1fr] gap-[6px] flex-1 min-h-0 overflow-hidden'
            : 'grid grid-cols-1 gap-[6px] flex-1 min-h-0'
        }
      >
        {/* LEFT */}
        <div className="studio-panel dub-panel-col">
          {dubVideoFile ? (
            <>
              <WaveformTimeline
                audioSrc={dubLocalBlobUrl?.audioUrl}
                videoSrc={dubLocalBlobUrl?.videoUrl}
                segments={[]}
                disabled={true}
                overlayContent={
                  dubStep === 'uploading' ? (
                    <PrepOverlay
                      stage={dubPrepStage}
                      progress={dubPrepProgress}
                      onAbort={handleDubAbort}
                    />
                  ) : dubStep === 'installing-asr' ? (
                    <AsrInstallStatus t={t} install={asrInstall} onAbort={handleDubAbort} />
                  ) : dubStep === 'transcribing' ? (
                    <TranscribeOverlay
                      elapsed={transcribeElapsed}
                      progress={transcribeProgress}
                      duration={dubDuration}
                      onAbort={handleDubAbort}
                    />
                  ) : null
                }
              />
              <div className="flex gap-[8px] mt-[8px] items-center">
                <label
                  htmlFor="video-upload"
                  className="flex items-center gap-[6px] px-[12px] py-[6px] bg-[rgba(255,255,255,0.05)] [border:1px_solid_rgba(255,255,255,0.1)] rounded-[6px] cursor-pointer text-[0.8rem] text-fg-muted"
                >
                  <Film size={13} /> {t('dub.change_file')}
                </label>
                {dubJobId && handleDubImportSrt && (
                  <label
                    htmlFor="srt-import-input"
                    className="flex items-center gap-[6px] px-[12px] py-[6px] bg-[rgba(255,255,255,0.05)] [border:1px_solid_rgba(255,255,255,0.1)] rounded-[6px] cursor-pointer text-[0.8rem] text-fg-muted"
                    title={t('dub.import_srt')}
                    style={{ cursor: 'pointer' }}
                  >
                    <FileText size={13} /> {t('dub.import_srt')}
                    <input
                      id="srt-import-input"
                      type="file"
                      accept=".srt,text/srt,text/plain"
                      hidden
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) handleDubImportSrt(f);
                        e.target.value = '';
                      }}
                    />
                  </label>
                )}
                <label className="inline-flex items-center gap-[5px] text-[12px] text-[var(--muted,#a89984)] whitespace-nowrap">
                  <Globe size={13} /> {t('dub.source_language')}
                  <SearchableSelect
                    ariaLabel={t('dub.source_language')}
                    value={dubSourceLangCode}
                    options={sourceOptions}
                    onChange={setDubSourceLangCode}
                    menuPortal
                    disabled={['uploading', 'transcribing', 'installing-asr'].includes(dubStep)}
                  />
                </label>
                <label
                  className="inline-flex items-center gap-[5px] text-[12px] text-[var(--muted,#a89984)] whitespace-nowrap"
                  title={t('dub.num_speakers_help')}
                >
                  <Users size={13} /> {t('dub.num_speakers_label')}
                  <input
                    type="number"
                    min={1}
                    max={20}
                    step={1}
                    className={SPEAKERS_INPUT}
                    placeholder={t('dub.num_speakers_auto')}
                    value={dubNumSpeakers ?? ''}
                    disabled={dubStep === 'uploading' || dubStep === 'transcribing'}
                    onChange={(e) => {
                      const v = parseInt(e.target.value, 10);
                      setDubNumSpeakers(Number.isFinite(v) && v > 0 ? Math.min(v, 20) : null);
                    }}
                  />
                </label>
                <Button
                  variant="primary"
                  className="flex-1"
                  onClick={handleDubUpload}
                  disabled={
                    dubStep === 'uploading' ||
                    dubStep === 'transcribing' ||
                    dubStep === 'installing-asr'
                  }
                >
                  {dubStep === 'uploading' || dubStep === 'transcribing' ? (
                    <>
                      <Loader className="spinner" size={14} /> {t('common.loading')}
                    </>
                  ) : (
                    <>
                      <Sparkles size={14} /> {t('dub.upload_transcribe')}
                    </>
                  )}
                </Button>
              </div>
            </>
          ) : dubStep === 'uploading' ? (
            <PrepOverlay
              stage={dubPrepStage}
              progress={dubPrepProgress}
              onAbort={handleDubAbort}
              large
            />
          ) : dubStep === 'installing-asr' ? (
            <div className="flex-1 flex flex-col items-center justify-center min-h-0">
              <AsrInstallStatus t={t} install={asrInstall} onAbort={handleDubAbort} />
            </div>
          ) : dubStep === 'transcribing' ? (
            // URL-ingest / restored jobs have no local `dubVideoFile`, so the
            // waveform-overlay branch above never runs for them. Without this
            // case the no-file path fell straight through to the idle dropzone
            // while the pipeline was actively transcribing: the stepper showed
            // Transcribe (active) but the main pane still showed the "Drop
            // video here" dropzone. Render the transcribe progress here too so
            // the main view always reflects the pipeline stage, on every path.
            <div className="flex-1 flex flex-col items-center justify-center min-h-0">
              <TranscribeOverlay
                elapsed={transcribeElapsed}
                progress={transcribeProgress}
                duration={dubDuration}
                onAbort={handleDubAbort}
              />
            </div>
          ) : dubStep === 'idle' ? (
            <>
              {!demoDismissed && <DubbingDemo onDismiss={dismissDubDemo} />}
              <div className="dub-start-card">
                <div
                  className="dub-idle-drop dub-start-drop"
                  role="group"
                  aria-labelledby="dub-start-title"
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.currentTarget.classList.add('is-dragging');
                  }}
                  onDragLeave={(e) => {
                    e.currentTarget.classList.remove('is-dragging');
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    e.currentTarget.classList.remove('is-dragging');
                    const file = e.dataTransfer.files[0];
                    if (
                      file &&
                      (file.type.startsWith('video/') ||
                        file.type.startsWith('audio/') ||
                        /\.(mp3|wav|flac|m4a|aac|ogg|opus|wma)$/i.test(file.name))
                    ) {
                      setDubVideoFile(file);
                      // #119: an audio file → audio-only dubbing (skip video work, output audio).
                      setDubInputType(
                        file.type.startsWith('audio/') ||
                          /\.(mp3|wav|flac|m4a|aac|ogg|opus|wma)$/i.test(file.name)
                          ? 'audio'
                          : 'video',
                      );
                      setDubStep('idle');
                      fileToMediaUrl(file, null).then((urls) => setDubLocalBlobUrl(urls));
                    }
                  }}
                >
                  <div className="dub-idle-drop__puck dub-start-mark" aria-hidden="true">
                    <UploadCloud size={28} />
                  </div>
                  <div className="text-center">
                    <div id="dub-start-title" className="dub-start-title text-fg font-medium">
                      {t('dub.drop_here')}
                    </div>
                    <div className="dub-start-formats text-fg-muted">
                      {t('dub.supported_formats')}
                    </div>
                  </div>
                  <Button
                    variant="primary"
                    size="sm"
                    className="dub-start-choose"
                    onClick={() => videoUploadInputRef.current?.click()}
                    leading={<UploadCloud size={14} aria-hidden="true" />}
                  >
                    {t('gallery.upload')}
                  </Button>
                </div>

                <div className="dub-start-url">
                  <Link2 size={15} aria-hidden="true" />
                  <input
                    type="url"
                    aria-label={t('dub.paste_url')}
                    placeholder={t('dub.paste_url')}
                    value={ingestUrl}
                    onChange={(e) => setIngestUrl(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        onIngestUrl();
                      }
                    }}
                    className="input-base flex-1"
                  />
                  <button
                    type="button"
                    onClick={onIngestUrl}
                    disabled={!ingestUrl.trim()}
                    className={`dub-ingest-row__cta ${ingestUrl.trim() ? 'is-ready' : ''}`}
                  >
                    {t('dub.ingest')}
                  </button>
                </div>

                <div className="dub-start-languages">
                  <label className="dub-landing-opts__lang inline-flex min-w-0 text-[var(--chrome-fg-muted)]">
                    <Globe size={13} aria-hidden="true" />
                    <span className="text-[0.72rem] font-medium">{t('dub.source_language')}</span>
                    <SearchableSelect
                      ariaLabel={t('dub.source_language')}
                      value={dubSourceLangCode}
                      options={sourceOptions}
                      onChange={setDubSourceLangCode}
                      menuPortal
                      buttonClassName="input-base dub-start-language-select"
                    />
                  </label>
                  <label className="dub-landing-opts__lang inline-flex min-w-0 text-[var(--chrome-fg-muted)]">
                    <Globe size={13} aria-hidden="true" />
                    <span className="text-[0.72rem] font-medium">{t('dub.target_language')}</span>
                    <SearchableSelect
                      ariaLabel={t('dub.target_language')}
                      value={dubLangCode}
                      options={languageOptions}
                      onChange={(code) => {
                        setDubLangCode(code);
                        const language = LANG_CODES.find((item) => item.code === code);
                        if (language) setDubLang(language.label);
                      }}
                      menuPortal
                      buttonClassName="input-base dub-start-language-select"
                    />
                  </label>
                </div>

                <button
                  type="button"
                  className="dub-start-advanced"
                  onClick={() => setLandingAdvOpen((open) => !open)}
                  aria-expanded={landingAdvOpen}
                  aria-controls="dub-start-advanced-options"
                >
                  <span>{t('dub.advanced')}</span>
                  {landingAdvOpen ? (
                    <ChevronUp size={13} aria-hidden="true" />
                  ) : (
                    <ChevronDown size={13} aria-hidden="true" />
                  )}
                </button>

                <div
                  id="dub-start-advanced-options"
                  className="dub-start-options"
                  hidden={!landingAdvOpen}
                >
                  <label className="dub-start-caption-option" title={t('dub.pull_captions_title')}>
                    <input
                      type="checkbox"
                      className="m-0 accent-[var(--color-brand)]"
                      checked={fetchYtSubs}
                      onChange={(e) => setFetchYtSubs(e.target.checked)}
                    />
                    <FileText size={13} aria-hidden="true" />
                    <span>{t('dub.pull_captions')}</span>
                  </label>

                  <div className="dub-start-cookie-option">
                    <span>{t('dub.youtube_auth')}</span>
                    <input
                      ref={youtubeCookieInputRef}
                      type="file"
                      accept=".txt,text/plain"
                      aria-label={t('dub.youtube_cookie_file')}
                      onChange={(e) => setYoutubeCookieFile(e.target.files?.[0] || null)}
                    />
                    {youtubeCookieFile && (
                      <button
                        type="button"
                        className="text-fg-muted hover:text-fg"
                        onClick={() => setYoutubeCookieFile(null)}
                        aria-label={t('dub.remove_cookie_file')}
                      >
                        ×
                      </button>
                    )}
                  </div>

                  <div className="dub-start-generation-options">
                    <label className="dub-landing-adv__field" title={t('dub.num_speakers_help')}>
                      <span>
                        <Users size={12} aria-hidden="true" /> {t('dub.num_speakers_label')}
                      </span>
                      <input
                        type="number"
                        min={1}
                        max={20}
                        step={1}
                        className={SPEAKERS_INPUT}
                        placeholder={t('dub.num_speakers_auto')}
                        value={dubNumSpeakers ?? ''}
                        onChange={(e) => {
                          const value = parseInt(e.target.value, 10);
                          setDubNumSpeakers(
                            Number.isFinite(value) && value > 0 ? Math.min(value, 20) : null,
                          );
                        }}
                      />
                    </label>
                    <label className="dub-landing-adv__field dub-landing-adv__field--grow">
                      <span>
                        <UserSquare2 size={12} aria-hidden="true" /> {t('dub.style')}
                      </span>
                      <input
                        type="text"
                        className="input-base"
                        placeholder={t('dub.style_placeholder')}
                        value={dubInstruct}
                        onChange={(e) => setDubInstruct(e.target.value)}
                      />
                    </label>
                  </div>
                </div>

                <button
                  type="button"
                  data-testid="dub-open-batch-queue"
                  className="dub-start-batch"
                  onClick={() => onOpenQueue?.()}
                >
                  <Activity size={12} aria-hidden="true" />
                  <span>{t('dub.batch_queue_link')}</span>
                </button>
              </div>
            </>
          ) : (
            // Any other active pipeline step reaching the no-file path (e.g.
            // `stopping` after a URL-ingested generation) must never fall back
            // to the idle dropzone — keep a neutral working indicator instead.
            <div className="flex-1 flex flex-col items-center justify-center min-h-0">
              <Loader className="spinner" size={24} color="#d3869b" />
            </div>
          )}

          <input
            ref={videoUploadInputRef}
            type="file"
            accept="video/*,audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma"
            id="video-upload"
            className="hidden"
            disabled={
              dubStep === 'uploading' || dubStep === 'transcribing' || dubStep === 'installing-asr'
            }
            onChange={(e) => {
              const file = e.target.files[0];
              if (!file) return;
              setDubVideoFile(file);
              // #119: an audio file → audio-only dubbing (skip video work, output audio).
              setDubInputType(
                file.type.startsWith('audio/') ||
                  /\.(mp3|wav|flac|m4a|aac|ogg|opus|wma)$/i.test(file.name)
                  ? 'audio'
                  : 'video',
              );
              setDubStep('idle');
              fileToMediaUrl(file, dubLocalBlobUrl).then((urls) => setDubLocalBlobUrl(urls));
            }}
          />

          {dubVideoFile && (
            <div className="dub-cast dub-cast--muted mt-[2px] px-[var(--space-3)] py-[3px] bg-[var(--chrome-bg)] rounded-[var(--chrome-radius-pill)] [border:1px_solid_var(--chrome-border)]">
              <div className="flex gap-[var(--space-2)] items-center flex-wrap">
                <span className="dub-cast__kicker font-[family-name:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] text-[var(--chrome-fg-muted)] tracking-[var(--chrome-label-track)] uppercase font-semibold">
                  {t('dub.cast')}
                </span>
                <span className="dub-cast__label font-[family-name:var(--chrome-font-mono)] text-[0.62rem] text-[var(--chrome-fg)]">
                  {t('dub.speaker', { n: 1 })}
                </span>
                <span className="font-[family-name:var(--chrome-font-mono)] text-[0.62rem] text-[var(--chrome-fg-dim)] px-[6px] py-[1px] bg-transparent [border:1px_solid_var(--chrome-border)] rounded-[var(--chrome-radius-pill)]">
                  {t('dub.default')}
                </span>
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: Ghost settings + segment table (only when video loaded) */}
        {dubVideoFile ? (
          <div className="studio-panel dub-panel-col">
            <div className="flex gap-[4px] mb-[4px] flex-wrap items-end opacity-40">
              <div className="flex-1 min-w-[90px]">
                <div className="label-row">
                  <Globe className="label-icon" size={9} /> {t('dub.language')}
                </div>
                <SearchableSelect
                  ariaLabel={t('dub.language')}
                  value="auto"
                  options={[{ value: 'auto', label: t('dub.auto') }]}
                  disabled
                />
              </div>
              <div className="flex-1 min-w-[80px]">
                <div className="label-row">{t('dub.iso_code')}</div>
                <SearchableSelect
                  ariaLabel={t('dub.iso_code')}
                  value="en"
                  options={[{ value: 'en', label: `en — ${t('dub.original_audio')}` }]}
                  disabled
                />
              </div>
              <div className="flex-1 min-w-[90px]">
                <div className="label-row">
                  <UserSquare2 className="label-icon" size={9} /> {t('dub.style')}
                </div>
                <input
                  className="input-base text-[0.65rem]"
                  disabled
                  placeholder={t('dub.style_placeholder')}
                />
              </div>
              <button
                disabled
                className="px-[8px] py-[3px] bg-[rgba(131,165,152,0.08)] [border:1px_solid_rgba(131,165,152,0.12)] text-[#504945] rounded-[4px] text-[0.62rem] flex items-center gap-[3px] whitespace-nowrap"
              >
                <Languages size={10} /> {t('dub.translate_all')}
              </button>
            </div>
            <div className="mb-[4px]">
              <div className="override-toggle dub-skel-transcript-toggle__inner">
                <span>
                  <FileText size={10} className="align-middle mr-[3px]" /> {t('dub.transcript')}
                </span>
                <ChevronDown size={10} />
              </div>
            </div>
            <div className="segment-table dub-skel-table">
              <div className="segment-header">
                <span className="w-[55px] flex-[0_0_55px]">{t('dub.time_col')}</span>
                <span className="w-[44px] flex-[0_0_44px]">{t('dub.spkr_col')}</span>
                <span className="flex-1">{t('dub.text_col')}</span>
                <span className="w-[70px] flex-[0_0_70px]">{t('dub.voice_col')}</span>
                <span className="w-[40px] flex-[0_0_40px]"></span>
              </div>
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <div
                  key={i}
                  className="segment-row dub-skel-row"
                  style={{ opacity: 0.5 + 0.07 * (6 - i) }}
                >
                  <span className="dub-skel-bar w-[55px] flex-[0_0_55px]" />
                  <span className="dub-skel-bar w-[44px] flex-[0_0_44px]" />
                  <div className="dub-skel-bar flex-1 min-w-0" />
                  <span className="dub-skel-bar w-[70px] flex-[0_0_70px]" />
                  <div className="flex gap-[1px] w-[40px] flex-[0_0_40px]">
                    <span className="segment-del dub-skel-cell-acts__icon">
                      <Trash2 size={9} />
                    </span>
                  </div>
                </div>
              ))}
              <div className="px-[8px] pt-[10px] pb-[4px] text-[0.62rem] text-[var(--chrome-fg-dim)] text-center">
                {t('dub.transcript_after_extract', {
                  defaultValue: 'Transcript appears after extraction.',
                })}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {/* Ghost footer — only once a file is in play; the bare landing stays
              clean. Generate is the lone primary, exports demoted to one menu. */}
      {dubVideoFile && (
        <div className="studio-panel px-[8px] py-[4px] shrink-0">
          <div className="flex gap-[4px]">
            <Button variant="primary" className="flex-1 opacity-40" disabled>
              <Play size={11} /> {t('dub.generate_dub')}
            </Button>
            <button
              className="inline-flex items-center gap-[5px] bg-transparent border border-transparent text-[var(--chrome-fg-muted)] rounded-[8px] flex-[0_0_auto] px-[8px] py-[4px] text-[0.7rem] opacity-40"
              disabled
            >
              <Download size={11} /> {t('dub.export_btn', { defaultValue: 'Export' })}{' '}
              <ChevronDown size={10} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
