import {
  FileText,
  Save,
  RotateCcw,
  Loader,
  Square,
  Play,
  Download,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '../../ui';
import FooterBtn from './FooterBtn';
import DubPipelineStepper from './DubPipelineStepper';
import { formatTime } from '../../utils/format';

export default function DubHeader({
  t,
  dubFilename,
  dubDuration,
  dubSegments,
  activeProjectName,
  saveProject,
  resetDub,
  dubStep,
  handleDubStop,
  dubProgress,
  onGenerateClick,
  isTranslating,
  multiLangMode,
  multiLangs,
  incrementalPlan,
  handleDubGenerate,
  qcRunning,
  handleDubQc,
  setExportOpen,
  pipelineSteps,
  onPipelineStep,
}) {
  const generateLabel =
    multiLangMode && multiLangs.length > 1
      ? t('dub.generate_dub_multi', {
          count: multiLangs.length,
          defaultValue: 'Generate {{count}} dubs',
        })
      : t('dub.generate_dub');
  const workflowActionBusy =
    qcRunning || isTranslating || dubStep === 'generating' || dubStep === 'stopping';

  return (
    <div
      className="dub-command-bar"
      data-testid="dub-command-bar"
      role="toolbar"
      aria-label={t('dub.video_dubbing_studio')}
    >
      <div className="dub-command-bar__identity">
        <span className="dub-command-bar__file" aria-hidden="true">
          <FileText size={13} />
        </span>
        <div className="min-w-0">
          <div className="dub-command-bar__title" title={dubFilename}>
            {dubFilename}
          </div>
          <div className="dub-command-bar__meta">
            <span>{formatTime(dubDuration)}</span>
            <span aria-hidden="true">·</span>
            <span>
              {dubSegments.length} {t('dub.segs')}
            </span>
            {activeProjectName && activeProjectName !== dubFilename && (
              <>
                <span aria-hidden="true">·</span>
                <span className="dub-command-bar__project" title={activeProjectName}>
                  {activeProjectName}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      <DubPipelineStepper
        dubStep={dubStep}
        inline
        variant="command"
        selectableSteps={pipelineSteps}
        onStepSelect={onPipelineStep}
      />

      <div className="dub-command-bar__actions [.shell-mini_&]:col-[1/-1] [.shell-mini_&]:row-start-3 [.shell-mini_&]:w-full [.shell-mini_&]:flex-wrap">
        <div className="dub-command-bar__utilities">
          <Button
            variant="subtle"
            size="sm"
            className="!size-[26px] !p-0"
            onClick={saveProject}
            title={t('dub.save')}
            aria-label={t('dub.save')}
          >
            <Save size={12} aria-hidden="true" />
          </Button>
          <Button
            variant="danger"
            size="sm"
            className="!size-[26px] !p-0"
            onClick={resetDub}
            title={t('dub.reset')}
            aria-label={t('dub.reset')}
          >
            <RotateCcw size={12} aria-hidden="true" />
          </Button>
        </div>

        <div
          className="flex flex-wrap items-center justify-end gap-[4px] p-[3px] [.shell-mini_&]:w-full"
          data-testid="dub-primary-actions"
        >
          {dubStep === 'stopping' ? (
            <FooterBtn
              sm
              tone="stopping"
              disabled
              className="dub-command-bar__primary flex-none! [.shell-mini_&]:flex-1!"
              icon={<Loader className="spinner" size={10} aria-hidden="true" />}
              label={t('dub.stopping')}
              aria-busy="true"
            />
          ) : dubStep === 'generating' ? (
            <FooterBtn
              sm
              tone="danger"
              className="dub-command-bar__primary flex-none! hover:-translate-y-px active:translate-y-0 motion-reduce:transform-none [.shell-mini_&]:flex-1!"
              onClick={handleDubStop}
              icon={<Square size={9} aria-hidden="true" />}
              label={t('dub.stop_progress', {
                current: dubProgress.current,
                total: dubProgress.total,
              })}
            />
          ) : (
            <>
              <FooterBtn
                sm
                tone={dubSegments.length && !isTranslating ? 'pink' : 'idle'}
                className="dub-command-bar__primary dub-action-btn--generate flex-none! enabled:hover:-translate-y-px enabled:active:translate-y-0 motion-reduce:transform-none enabled:shadow-[0_5px_14px_color-mix(in_srgb,var(--chrome-accent)_18%,transparent)] enabled:hover:shadow-[0_7px_18px_color-mix(in_srgb,var(--chrome-accent)_26%,transparent)] [.shell-mini_&]:flex-1!"
                onClick={onGenerateClick}
                disabled={!dubSegments.length || workflowActionBusy}
                icon={<Play className="fill-current" size={11} aria-hidden="true" />}
                label={generateLabel}
                aria-label={generateLabel}
              />
              {dubStep === 'done' && incrementalPlan && incrementalPlan.stale?.length > 0 && (
                <FooterBtn
                  sm
                  tone="pink"
                  className="flex-none! enabled:hover:-translate-y-px enabled:active:translate-y-0 motion-reduce:transform-none [.shell-mini_&]:flex-1!"
                  disabled={workflowActionBusy}
                  onClick={() =>
                    handleDubGenerate({ regenOnly: incrementalPlan.stale, preview: true })
                  }
                  icon={<Play className="fill-current" size={11} aria-hidden="true" />}
                  label={t('dub.regen_changed', { count: incrementalPlan.stale.length })}
                />
              )}
            </>
          )}

          {dubStep === 'done' && (
            <FooterBtn
              sm
              tone="idle"
              className="dub-action-btn--verify flex-none! enabled:hover:-translate-y-px enabled:active:translate-y-0 motion-reduce:transform-none enabled:hover:shadow-[var(--shadow-sm)] [.shell-mini_&]:flex-1!"
              disabled={workflowActionBusy || !dubSegments.length}
              onClick={handleDubQc}
              icon={
                qcRunning ? (
                  <Loader className="spinner" size={11} aria-hidden="true" />
                ) : (
                  <ShieldCheck size={11} aria-hidden="true" />
                )
              }
              label={t('dub.verify')}
              title={t('dub.qc_btn')}
              aria-label={t('dub.qc_btn')}
              aria-busy={qcRunning || undefined}
            />
          )}
          <FooterBtn
            sm
            tone={dubStep === 'done' ? 'green' : 'idle'}
            className="dub-action-btn--export flex-none! font-semibold enabled:hover:-translate-y-px enabled:active:translate-y-0 motion-reduce:transform-none enabled:shadow-[0_5px_14px_color-mix(in_srgb,var(--chrome-severity-ok)_13%,transparent)] enabled:hover:shadow-[0_7px_18px_color-mix(in_srgb,var(--chrome-severity-ok)_20%,transparent)] [.shell-mini_&]:flex-1!"
            disabled={workflowActionBusy || (dubStep !== 'done' && !dubSegments.length)}
            onClick={() => setExportOpen(true)}
            icon={<Download size={12} aria-hidden="true" />}
            label={t('dub.export_btn')}
            title={t('dub.export_btn')}
            aria-label={t('dub.export_btn')}
          />
        </div>
      </div>
    </div>
  );
}
