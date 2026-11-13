import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, PlayIcon, SquareIcon, RotateCcwIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { saveLocalFile } from '@/lib/local-export';
import { describeError } from '@/lib/api/client';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
import { useEngines } from '@/hooks/use-engines';
import { EngineNotice } from '@/components/engine-notice';
import type { WorkflowDocument } from './workflow-model';
import { compileWorkflow, executeWorkflow, outputName, prepareRun, WorkflowValidationError, type WorkflowRun } from './workflow-runtime';
import { workflowOperations } from './workflow-operations';
import { readWorkflowRun, writeWorkflowRun } from './workflow-run-store';

function AudioOutput({ audio }: { audio: Blob }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const next = URL.createObjectURL(audio);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [audio]);
  return <audio controls preload="none" src={url} className="h-8 w-full" />;
}

export function WorkflowRunner({ document, onClose, onBusy, storageReady = true }: {
  document: WorkflowDocument; onClose(): void; onBusy(busy: boolean): void; storageReady?: boolean;
}) {
  const { t } = useTranslation();
  const engines = useEngines();
  const blocker = useTtsReadiness();
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  let plan: ReturnType<typeof compileWorkflow> | null = null;
  let validation = '';
  try { plan = compileWorkflow(document, JSON.stringify(engines.data ? Object.entries(engines.data).map(([family, state]) => [family, state.active, state.active_model]) : [])); }
  catch (error) { validation = t(`workflowRun.invalid_${error instanceof WorkflowValidationError ? error.code : 'graph'}`); }
  useEffect(() => {
    mounted.current = true;
    void readWorkflowRun(document.id).then((saved) => {
      if (mounted.current) setRun(saved);
    }).catch((error) => { if (mounted.current) setError(describeError(error)); })
      .finally(() => { if (mounted.current) setLoading(false); });
    return () => { mounted.current = false; controller.current?.abort(); onBusy(false); };
  }, [document.id, onBusy]);
  const start = async (fresh = false) => {
    if (!storageReady || !plan || !engines.data || controller.current) return;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true); onBusy(true); setError('');
    try {
      const next = prepareRun(plan, fresh ? null : run);
      await executeWorkflow(plan, next, workflowOperations, abort.signal, async (checkpoint) => {
        // Save first. A quota failure stops inference instead of pretending recovery works.
        await writeWorkflowRun(document.id, checkpoint);
        if (mounted.current) setRun(checkpoint);
      });
    } catch (error) { if (mounted.current) setError(describeError(error)); }
    finally {
      controller.current = null;
      if (mounted.current) { setBusy(false); onBusy(false); }
    }
  };
  const needsSpeech = document.steps.some((step) => ['speak', 'convert'].includes(step.kind));
  const matches = plan?.signature === run?.signature;
  const done = run?.items.filter((item) => item.state === 'done').length ?? 0;
  return <section className="workflow-runner studio-scrollbar" aria-label={t('dub.export')}>
    <div className="workflow-panel-heading"><span>{t('dub.export')}</span>
      <Button size="icon-xs" variant="ghost" disabled={busy} onClick={onClose} aria-label={t('common.close')}><XIcon /></Button>
    </div>
    <div className="workflow-runner__body">
      {validation && <p role="alert" className="text-xs text-destructive">{validation}</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error.startsWith('workflowRun.') ? t(error) : error}</p>}
      {engines.isError && <p role="alert" className="text-xs text-destructive">{describeError(engines.error)}<Button size="sm" variant="ghost" onClick={engines.retry}>{t('common.retry')}</Button></p>}
      {needsSpeech && <EngineNotice operation="tts" />}
      <div className="flex gap-2">
        {busy ? <Button className="flex-1" variant="outline" onClick={() => controller.current?.abort()}><SquareIcon />{t('common.cancel')}</Button>
          : <Button className="flex-1" disabled={!storageReady || loading || !engines.data || !plan || (needsSpeech && blocker !== null)} onClick={() => void start(Boolean(matches && run && done === run.items.length))}><PlayIcon />{t(matches && run ? done === run.items.length ? 'workflowRun.restart' : 'common.retry' : 'workflowRun.run')}</Button>}
        {!busy && run && done < run.items.length && <Button size="icon-sm" variant="outline" disabled={!storageReady || !engines.data || !plan || (needsSpeech && blocker !== null)} onClick={() => void start(true)} aria-label={t('workflowRun.restart')} title={t('workflowRun.restart')}><RotateCcwIcon /></Button>}
      </div>
      {run && <>
        <p role="status" aria-live="polite" className="text-xs tabular-nums">{t('workflowRun.progress', { done, total: run.items.length })}</p>
        <progress max={run.items.length} value={done} className="h-1 w-full accent-primary" />
        {!matches && <p className="text-xs text-muted-foreground">{t('workflowRun.changed')}</p>}
        {run.items.map((item, index) => {
          const audio = item.outputStep ? item.audio[item.outputStep] : item.sourceId ? undefined : Object.values(item.audio).at(-1);
          const text = item.outputStep ? item.texts?.[item.outputStep] : undefined;
          const state = item.state === 'running' && !busy ? 'cancelled' : item.state;
          return <article key={index} className="workflow-run-output">
            <div className="flex items-center justify-between gap-2"><strong className="truncate text-xs">{item.name}</strong><span className="text-xs text-muted-foreground">{t(`batch.status_${state === "ready" ? "queued" : state}`)}</span></div>
            {busy && item.state === 'running' && <p className="text-xs text-muted-foreground">{t(`workflows.step_${document.steps.find((step) => step.id === item.stepId)?.kind ?? 'speak'}`)}</p>}
            {item.error && <p role="alert" className="text-xs text-destructive break-words">{item.error.startsWith('workflowRun.') ? t(item.error) : item.error}</p>}
            {text !== undefined && <><pre className="max-h-40 overflow-auto whitespace-pre-wrap text-xs">{text}</pre><Button size="sm" variant="ghost" onClick={() => {
              void saveLocalFile(new Blob([text], { type: 'text/plain;charset=utf-8' }), outputName(document.name, item.name, index, 'txt')).catch((error) => setError(describeError(error)));
            }}><DownloadIcon />{t('dub.export')}</Button></>}
            {audio && <><AudioOutput audio={audio} /><Button size="sm" variant="ghost" onClick={() => {
              void saveLocalFile(audio, outputName(document.name, item.name, index)).catch((error) => setError(describeError(error)));
            }}><DownloadIcon />{t('dub.export')}</Button></>}
          </article>;
        })}
      </>}
    </div>
  </section>;
}
