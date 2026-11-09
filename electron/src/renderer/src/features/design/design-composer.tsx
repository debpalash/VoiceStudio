import { LoaderCircleIcon, PlayIcon, XIcon } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import {
  EngineSetupButton,
  EngineSetupStatus,
  SetUpLink,
  useEngineSetup,
} from '@/components/engine-notice';
import { DisabledReason, firstReason } from '@/components/disabled-reason';
import { PipelineFailure } from '@/components/pipeline-failure';
import { Button } from '@/components/ui/button';
import { EngineLanguagePicker } from '@/features/clone/engine-language-picker';
import { QualityControls } from '@/features/clone/quality-controls';
import { VoiceControls } from '@/features/clone/action-bar';
import { QualityOffer } from '@/features/clone/quality-offer';
import type { DesignBlocker, UseGenerateClone } from '@/hooks/use-generate';
import { cn } from '@/lib/utils';

/**
 * The Voice Design composer: output language, quality and voice controls, and
 * one primary action. While text-to-speech is not ready that action is the
 * setup that fixes it, never a dead Synthesize button under a warning.
 */
export function DesignComposer({
  generation,
  blocker,
  ready,
  engineName,
  text = '',
  onSynthesize,
}: {
  generation: UseGenerateClone;
  blocker: DesignBlocker;
  /** The draft has what a render needs (script, mapped description). */
  ready: boolean;
  engineName: string;
  /** The script, for the render-time estimate. */
  text?: string;
  onSynthesize: () => void;
}) {
  const { t } = useTranslation();
  const statusId = useId();
  const setup = useEngineSetup('design');
  const { isGenerating } = generation;
  const needsSetup = blocker === 'engine' && !isGenerating;
  const progress = generation.stage === 'loading' ? generation.modelProgress : generation.progress;
  const label = isGenerating
    ? t(
        generation.stage === 'loading'
          ? generation.modelStage
            ? `synthesisState.${generation.modelStage}`
            : 'synthesisState.loading'
          : generation.stage === 'receiving'
            ? 'synthesisState.receiving'
            : generation.stage === 'preparing'
              ? 'synthesisState.preparing'
              : 'clone.generating_status',
      )
    : t('clone.synthesize');
  return (
    <div className="@container/composer mx-auto w-full max-w-4xl shrink-0 px-6 pt-3 pb-4">
      {needsSetup && <EngineSetupStatus setup={setup} id={statusId} />}
      {(blocker === 'design' || blocker === 'cloning') && !isGenerating && (
        <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2 px-1 text-sm text-muted-foreground">
          <p role="status">
            {t(
              blocker === 'cloning'
                ? 'designWorkspace.engine_cannot_reuse_sample'
                : 'designWorkspace.engine_cannot_design',
              { engine: engineName },
            )}
          </p>
          <SetUpLink family="tts" />
        </div>
      )}
      {blocker === 'loading' && !isGenerating && (
        <p className="mb-2.5 px-1 text-sm text-muted-foreground" role="status">
          {t('preferences.loading')}
        </p>
      )}
      {generation.error && (
        <PipelineFailure
          className="mb-2.5"
          fallback={generation.error}
          topic={generation.errorTopic}
          onDismiss={generation.clearError}
        />
      )}
      {/* Wraps instead of overlapping: the tools take their natural width and
          the action drops to its own line when the column is too narrow. */}
      <div
        data-testid="design-composer"
        className="glass-panel relative flex flex-wrap items-center gap-2 overflow-hidden rounded-xl border border-border/60 bg-muted/30 p-2.5"
      >
        <div className="flex min-w-0 flex-auto items-center gap-1">
          <EngineLanguagePicker operation="tts" />
          <QualityControls size="sm" disabled={isGenerating} />
          <VoiceControls size="icon-sm" />
        </div>
        <div className="ms-auto flex min-w-0 items-center gap-2 @max-[28rem]/composer:w-full">
          {needsSetup ? (
            <EngineSetupButton
              setup={setup}
              primary
              className="h-10 min-w-44 rounded-lg px-4 @max-[28rem]/composer:flex-1"
            />
          ) : (
            <DisabledReason
              className="@max-[28rem]/composer:flex-1"
              reason={
                isGenerating
                  ? null
                  : firstReason([
                      [
                        blocker === 'design' || blocker === 'cloning',
                        t(
                          blocker === 'cloning'
                            ? 'designWorkspace.engine_cannot_reuse_sample'
                            : 'designWorkspace.engine_cannot_design',
                          { engine: engineName },
                        ),
                      ],
                      [blocker === 'loading', t('preferences.loading')],
                      [!ready, t('disabledReason.text_required')],
                    ])
              }
            >
              {(describedBy) => (
                <Button
                  size="lg"
                  className={cn(
                    'h-10 min-w-44 gap-3 overflow-hidden rounded-lg ps-4 pe-3 @max-[28rem]/composer:flex-1',
                    isGenerating ? 'justify-between' : 'justify-center',
                  )}
                  disabled={!ready || isGenerating || blocker !== null}
                  aria-describedby={describedBy}
                  aria-busy={isGenerating}
                  aria-label={label}
                  onClick={onSynthesize}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {isGenerating ? (
                      <LoaderCircleIcon className="shrink-0 animate-spin motion-reduce:animate-none" />
                    ) : (
                      <PlayIcon className="shrink-0" />
                    )}
                    <span className="min-w-0 truncate" role={isGenerating ? 'status' : undefined}>
                      {label}
                    </span>
                  </span>
                  {isGenerating && (
                    <span className="shrink-0 text-xs font-normal tabular-nums opacity-80">
                      {`${progress == null ? '' : `${Math.round(progress)}% · `}${generation.elapsedSeconds.toFixed(1)}s`}
                    </span>
                  )}
                </Button>
              )}
            </DisabledReason>
          )}
          {isGenerating && (
            <Button
              size="icon-lg"
              className="size-10 shrink-0"
              variant="outline"
              onClick={generation.cancel}
              aria-label={t('clone.cancel_generation')}
            >
              <XIcon />
            </Button>
          )}
        </div>
        {isGenerating && (
          <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-muted">
            <div
              className={
                progress == null
                  ? 'h-full w-full animate-pulse bg-primary motion-reduce:animate-none'
                  : 'h-full bg-primary transition-[width] duration-200'
              }
              style={progress == null ? undefined : { width: `${progress}%` }}
            />
          </div>
        )}
      </div>
      {!needsSetup && (
        <QualityOffer text={text} isGenerating={isGenerating} className="mt-2 justify-end px-1" />
      )}
    </div>
  );
}
