import { DubScriptComparison } from './dub-script-comparison';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, LayersIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { saveNativeData } from '@/lib/native-save';
import { getBridge } from '@/components/bridge';
import { describeError } from '@/lib/api/client';
import { dubGuideState } from './dub-project-guide';
import { compareNarration, narrationFile } from './dub-narration';
import {
  setDubNarrationEnabled,
  restoreDubNarrationWindows,
  setDubProduction,
  type DubSession,
} from './dub-session';

export function DubNarrationPanel({
  session,
  code,
  disabled,
  onLocate,
}: {
  onLocate?: (id: string) => void;
  session: DubSession;
  code?: string;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState('');
  if (!session.jobId || (!session.segments.length && !session.narration?.enabled)) return null;
  const active = Boolean(session.narration?.enabled),
    captions = session.narration?.captions || session.segments;
  const canPrepare = dubGuideState(session, code).textReady;
  const maxDrift = Math.max(0, ...session.segments.map((b) => b.fit_status?.drift_s || 0));
  const overrun = Math.max(
    0,
    ...session.segments.map((b) => (b.fit_status?.placed_end || 0) - (session.duration || 0)),
  );
  const download = async () => {
    if (!session.narration) return;
    setSaving(true);
    setError('');
    try {
      const data = narrationFile({ ...session.narration, blocks: session.segments });
      const name =
        (session.filename.replace(/\.[^.]+$/, '') || 'narration') +
        '.' +
        session.narration.language +
        '.dub.json';
      if (getBridge()) {
        const saved = await saveNativeData({
          suggestedName: name,
          data: new TextEncoder().encode(data),
        });
        if (saved && !saved.canceled && saved.path) setSavedPath(saved.path);
      } else {
        const url = URL.createObjectURL(new Blob([data], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = name;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
      }
    } catch (error) {
      setError(describeError(error));
    } finally {
      setSaving(false);
    }
  };
  return (
    <details
      open={!dubGuideState(session, code).audioReady}
      data-slot="dub-narration"
      className="shrink-0 space-y-2 border-b bg-muted/15 px-4 py-3"
    >
      <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium">
        <LayersIcon className="size-4" />
        {t(session.narrationWanted || active ? 'dub.flow_voice_script' : 'dub.narration_title')}
      </summary>
      <p className="text-xs text-muted-foreground">
        {t(active ? 'dub.narration_active_hint' : 'dub.narration_intro')}
      </p>
      {active && compareNarration(captions, session.segments).some((row) => !row.matches) && (
        <div role="alert" className="space-y-2 text-xs text-destructive">
          <p>{t('dub.compare_mismatch')}</p>
          <Button
            size="xs"
            variant="outline"
            disabled={disabled}
            onClick={() => restoreDubNarrationWindows()}
          >
            {t('dub.restore_srt_times')}
          </Button>
        </div>
      )}
      {active && (
        <p className="text-xs font-medium text-primary">
          {t('dub.narration_counts', {
            blocks: session.segments.length,
            captions: captions.length,
          })}
        </p>
      )}
      {!active && !canPrepare && (
        <p className="text-xs text-muted-foreground">{t('dub.narration_need_text')}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <DubScriptComparison
          captions={captions}
          blocks={active ? session.segments : []}
          onLocate={onLocate}
        />
        <Button
          size="xs"
          variant={active ? 'outline' : 'secondary'}
          disabled={disabled || (!active && !canPrepare)}
          onClick={() => setDubNarrationEnabled(!active)}
        >
          {t(active ? 'dub.narration_use_captions' : 'dub.narration_prepare')}
        </Button>
        {!active && session.narrationWanted && (
          <Button
            size="xs"
            variant="outline"
            disabled={disabled}
            onClick={() => setDubProduction({ narrationWanted: false })}
          >
            {t('dub.narration_use_captions')}
          </Button>
        )}
        {active && (
          <Button
            size="xs"
            variant="outline"
            disabled={disabled || saving}
            onClick={() => void download()}
          >
            <DownloadIcon />
            {t('dub.narration_save')}
          </Button>
        )}
      </div>
      {active && (
        <details className="text-xs">
          <summary className="cursor-pointer">{t('dub.narration_original_captions')}</summary>
          <div className="mt-2 max-h-32 overflow-auto space-y-1">
            {captions.map((cue, index) => (
              <p key={cue.id}>
                <span className="text-muted-foreground">
                  {index + 1} · {cue.start.toFixed(2)}–{cue.end.toFixed(2)}{' '}
                </span>
                {cue.text}
              </p>
            ))}
          </div>
        </details>
      )}
      {active && maxDrift > 1 && (
        <p className="text-xs text-warning">
          {t('dub.narration_drift', { seconds: maxDrift.toFixed(1) })}
        </p>
      )}
      {active && overrun > 0.1 && (
        <p className="text-xs text-warning">
          {t('dub.narration_overrun', { seconds: overrun.toFixed(1) })}
        </p>
      )}
      {savedPath && (
        <div role="status" className="space-y-1 text-xs">
          <p>{t('dub.export_saved')}</p>
          <p className="break-all select-text">{savedPath}</p>
          <Button
            size="xs"
            variant="outline"
            onClick={() =>
              void getBridge()
                ?.files.revealPath(savedPath)
                .catch((error) => setError(describeError(error)))
            }
          >
            {t('dub.export_show_folder')}
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </details>
  );
}
