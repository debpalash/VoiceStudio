import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import en from '@/i18n/locales/en.json';
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position,
  ReactFlow, ReactFlowProvider, useNodesState,
  type Connection, type Edge, type Node, type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  BotIcon, CopyIcon, FlagIcon, GitBranchIcon, MessageSquareIcon,
  PhoneCallIcon, PlayIcon, PlusIcon, Trash2Icon, WorkflowIcon, WandSparklesIcon, AudioLinesIcon, MicIcon, LanguagesIcon, ArrowLeftRightIcon,
} from 'lucide-react';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { CallsPage } from '@/features/calls/calls-page';
import {
  WORKFLOW_STORAGE_KEY, STEP_KINDS, addWorkflowStep, connectWorkflowSteps,
  duplicateWorkflowName, makeCallWorkflow, makeWorkflow, parseWorkflowLibrary, removedWorkflowData, removeWorkflowStep,
  repairWorkflowName,
  type StepKind, type WorkflowDocument, type WorkflowLibrary, type WorkflowStep,
} from './workflow-model';
import './workflows.css';
import { describeError } from '@/lib/api/client';
import { useProfiles } from '@/hooks/use-profiles';
import { WorkflowRunner } from './workflow-runner';
import { WorkflowInputs } from './workflow-inputs';
import { makeSpeechWorkflow, makeProcessingWorkflow } from './workflow-model';
import { deleteWorkflowArtifacts, deleteWorkflowRun } from './workflow-run-store';

const icons = {
  start: PlayIcon, agent: BotIcon, speak: MessageSquareIcon,
  condition: GitBranchIcon, call: PhoneCallIcon, normalize: WandSparklesIcon, audio: AudioLinesIcon, transcribe: MicIcon, translate: LanguagesIcon, convert: ArrowLeftRightIcon, end: FlagIcon,
};
type CanvasNode = Node<{ step: WorkflowStep; label: string; summary: string }>;
const nodeTypes = { workflowStep: StepCard };

function StepCard({ data, selected }: NodeProps<CanvasNode>) {
  const { t } = useTranslation();
  const Icon = icons[data.step.kind];
  return <div className={`workflow-node workflow-node--${data.step.kind}${selected ? ' is-selected' : ''}`}>
    {!['start', 'audio'].includes(data.step.kind) && <Handle type="target" position={Position.Left} />}
    <span className="workflow-node__icon"><Icon size={18} aria-hidden="true" /></span>
    <span className="workflow-node__body">
      {data.step.title && <small>{data.label}</small>}<strong>{data.step.title || data.label}</strong>
      {data.summary && <span>{data.summary}</span>}
    </span>
    {data.step.kind === 'condition' ? <>
      <Handle type="source" id="yes" position={Position.Right} style={{ top: '35%' }} aria-label={t('common.yes')} />
      <Handle type="source" id="no" position={Position.Right} style={{ top: '70%' }} aria-label={t('common.no')} />
    </> : data.step.kind !== 'end' && <Handle type="source" position={Position.Right} />}
  </div>;
}

function readLibrary(untitled: string): WorkflowLibrary {
  try { return parseWorkflowLibrary(window.localStorage.getItem(WORKFLOW_STORAGE_KEY), untitled); }
  catch { return parseWorkflowLibrary(null, untitled); }
}

function WorkflowCanvas({ onCalls }: { onCalls: (call?: Pick<WorkflowStep, 'phone' | 'text'>) => void }) {
  const { t } = useTranslation();
  const untitled = t('workflows.untitled', { defaultValue: en.workflows.untitled });
  const [library, setStoredLibrary] = useState(() => readLibrary(untitled));
  const libraryRef = useRef(library);
  libraryRef.current = library;
  const setLibrary = useCallback((update: (current: WorkflowLibrary) => WorkflowLibrary) => {
    setStoredLibrary((current) => {
      const next = update(current);
      if (next === current) return current;
      const removed = removedWorkflowData(current, next);
      return removed.media.length || removed.runs.length
        ? { ...next, cleanup: [...(current.cleanup || []), removed] } : next;
    });
  }, []);
  const cleaning = useRef(false);
  const [cleanupError, setCleanupError] = useState('');
  const retryCleanup = useCallback(async () => {
    if (cleaning.current) return;
    cleaning.current = true;
    try {
      const current = libraryRef.current;
      // Draft changes and their deletion queue are one atomic localStorage write.
      window.localStorage.setItem(WORKFLOW_STORAGE_KEY, JSON.stringify(current));
      const batch = current.cleanup || [];
      for (const task of batch) await deleteWorkflowArtifacts(task);
      if (batch.length) setStoredLibrary((latest) => ({ ...latest, cleanup: (latest.cleanup || []).filter((task) => !batch.includes(task)) }));
      setCleanupError('');
    } catch (error) { setCleanupError(describeError(error)); }
    finally { cleaning.current = false; }
  }, []);
  const [runnerOpen, setRunnerOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const profiles = useProfiles();
  const previousUntitled = useRef(untitled);
  const cancelDelete = useRef<HTMLButtonElement>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  useEffect(() => {
    const previous = previousUntitled.current;
    previousUntitled.current = untitled;
    setLibrary((current) => {
      let changed = false;
      const documents = current.documents.map((item) => {
        const repaired = repairWorkflowName(item.name, untitled);
        const localeChanged = previous !== untitled && (item.autoNamed || item.name === previous);
        const name = localeChanged ? untitled : repaired;
        if (name !== item.name) {
          changed = true;
          return { ...item, name, autoNamed: item.autoNamed || item.name === 'workflows.untitled' };
        }
        return item;
      });
      return changed ? { ...current, documents } : current;
    });
  }, [untitled]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const templates = ['restaurant', 'reschedule', 'hours'] as const;
  const document = library.documents.find((item) => item.id === library.activeId) ?? library.documents[0];
  const selected = document.steps.find((step) => step.id === selectedId) ?? null;
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);

  useEffect(() => {
    try { window.localStorage.setItem(WORKFLOW_STORAGE_KEY, JSON.stringify(library)); }
    catch (error) { setCleanupError(describeError(error)); return; }
    void retryCleanup();
  }, [library, retryCleanup]);

  const mappedNodes = useMemo<CanvasNode[]>(() => document.steps.map((step) => ({
    id: step.id, type: 'workflowStep', position: step.position, selected: step.id === selectedId,
    data: {
      step, label: t(`workflows.step_${step.kind}`),
      summary: step.kind === 'audio' ? `${t('workflowRun.add_audio')} · ${step.media?.length || 0}`
        : step.kind === 'translate' ? [step.sourceLanguage, step.language].filter(Boolean).join(' → ')
        : step.kind === 'start' ? `${t('workflowRun.scripts')} · ${step.scripts?.length || step.text.split(/^\s*---\s*$/m).filter((text) => text.trim()).length}`
        : step.kind === 'end' ? t('dub.export')
        : ['speak', 'convert'].includes(step.kind) ? (profiles.data?.find((profile) => profile.id === step.voiceId)?.name || t('convert.pick_voice'))
        : step.kind === 'normalize' ? `${step.targetDb ?? -2} dBFS`
        : step.kind === 'call' ? ([step.phone, step.text].filter(Boolean).join(' · ') || t('workflows.add_details'))
        : step.kind === 'condition' ? (step.text
          ? `${t(`workflows.match_${step.match || 'contains'}`)} · ${step.text}` : t('workflows.add_details'))
          : step.text || t('workflows.add_details'),
    },
  })), [document.steps, selectedId, t, profiles.data]);
  useEffect(() => setNodes(mappedNodes), [mappedNodes, setNodes]);

  const edges = useMemo<Edge[]>(() => document.connections.map((edge) => ({
    ...edge, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed },
  })), [document.connections]);
  const updateDocument = useCallback((update: (current: WorkflowDocument) => WorkflowDocument) => {
    setLibrary((current) => ({
      ...current,
      documents: current.documents.map((item) => item.id === current.activeId ? update(item) : item),
    }));
  }, []);
  const editStep = (change: Partial<WorkflowStep>) => {
    if (!selectedId) return;
    setLibrary((library) => ({ ...library, documents: library.documents.map((current) => current.id !== document.id ? current : ({
      ...current, updatedAt: Date.now(),
      steps: current.steps.map((step) => step.id === selectedId ? { ...step, ...change } : step),
    })) }));

  };
  const addStep = (kind: StepKind) => {
    const next = addWorkflowStep(document, kind);
    updateDocument(() => next);
    setSelectedId(next.steps[next.steps.length - 1].id);
  };
  const addWorkflow = () => {
    const next = makeWorkflow(untitled, true);
    setLibrary((current) => ({ ...current, activeId: next.id, documents: [...current.documents, next] }));
    setSelectedId(null);
  };
  const addTemplate = (template: typeof templates[number]) => {
    const next = makeCallWorkflow(t(`calls.example_${template}`), t(`calls.example_${template}_brief`));
    setLibrary((current) => ({ ...current, activeId: next.id, documents: [...current.documents, next] }));
    setSelectedId(next.steps[1].id);
  };
  const duplicateWorkflow = () => {
    const copy: WorkflowDocument = {
      ...structuredClone(document), id: crypto.randomUUID(),
      name: duplicateWorkflowName(document.name, library.documents),
      autoNamed: false, updatedAt: Date.now(),
    };
    setLibrary((current) => ({ ...current, activeId: copy.id, documents: [...current.documents, copy] }));
    setSelectedId(null);
  };
  const deleteWorkflow = async () => {
    if (!deletingId || deleteBusy || library.documents.length === 1) return;
    setDeleteBusy(true); setDeleteError('');
    try { await deleteWorkflowRun(deletingId); }
    catch (error) { setDeleteError(describeError(error)); setDeleteBusy(false); return; }
    setDeleteBusy(false);
    setLibrary((current) => {
      const documents = current.documents.filter((item) => item.id !== deletingId);
      return { ...current, activeId: documents[0].id, documents };
    });
    setSelectedId(null);
    setDeletingId(null);
  };

  return <div className="workflow-page">
    <WorkspaceHeader>
      <WorkflowIcon size={17} aria-hidden="true" />
      <h1 className="text-sm font-medium">{t('workflows.title')}</h1>
      <Button size="sm" variant="outline" disabled={running} onClick={() => onCalls()}><PhoneCallIcon aria-hidden="true" />{t('calls.title')}</Button>
    </WorkspaceHeader>
    {cleanupError && <div role="alert" className="flex items-center gap-2 px-4 text-xs text-destructive">{cleanupError}<Button size="sm" variant="ghost" onClick={() => void retryCleanup()}>{t('common.retry')}</Button></div>}
    <div className="workflow-intro">
      <div><h2>{t('workflows.heading')}</h2><p>{t('workflowRun.hint')}</p></div>
      <div className="workflow-intro__actions">
        <Button size="sm" onClick={() => { setSelectedId(null); setRunnerOpen(true); }}><PlayIcon size={15} />{t('workflowRun.run')}</Button>
        <Button size="sm" variant="outline" disabled={running} onClick={duplicateWorkflow}><CopyIcon size={15} />{t('workflows.duplicate')}</Button>
        <Button size="sm" variant="outline" onClick={() => { setDeleteError(''); setDeletingId(document.id); }} disabled={running || library.documents.length === 1}><Trash2Icon size={15} />{t('workflows.delete')}</Button>
      </div>
    </div>
    <div className={`workflow-workspace${selected || runnerOpen ? ' has-selection' : ''}`}>
      <aside className="workflow-rail studio-scrollbar" aria-label={t('workflows.library')}>
        <div className="workflow-panel-heading"><span>{t('workflows.library')}</span><Button size="icon-xs" variant="ghost" disabled={running} onClick={addWorkflow} aria-label={t('workflows.new')} title={t('workflows.new')}><PlusIcon /></Button></div>
        <div className="workflow-library-list">{library.documents.map((item) =>
          <button key={item.id} type="button" disabled={running} className={`workflow-library-item${item.id === document.id ? ' is-active' : ''}`} onClick={() => { setLibrary((current) => ({ ...current, activeId: item.id })); setSelectedId(null); }}><WorkflowIcon size={16} aria-hidden="true" /><span>{item.name}</span></button>,
        )}</div>
        <div className="workflow-panel-heading workflow-panel-heading--steps">{t('workflows.templates')}</div>
        <div className="workflow-templates">
          {[false, true].map((cleanup) => <button key={String(cleanup)} disabled={running} type="button" onClick={() => {
            const next = makeSpeechWorkflow(t(cleanup ? 'workflowRun.clean_recipe' : 'workflowRun.recipe'), t('workflowRun.sample'), cleanup);
            setLibrary((current) => ({ ...current, activeId: next.id, documents: [...current.documents, next] }));
            setRunnerOpen(false); setSelectedId(next.steps[0].id);
          }}><MessageSquareIcon size={15} /><span>{t(cleanup ? 'workflowRun.clean_recipe' : 'workflowRun.recipe')}</span><PlusIcon size={14} /></button>)}
          {([
            ['transcription_recipe', ['audio', 'transcribe', 'end']],
            ['conversion_recipe', ['audio', 'convert', 'end']],
            ['localization_recipe', ['audio', 'transcribe', 'translate', 'speak', 'end']],
          ] as [string, StepKind[]][]).map(([label, kinds]) => <button key={label} disabled={running} type="button" onClick={() => {
            const next = makeProcessingWorkflow(t('workflowRun.' + label), kinds);
            setLibrary((current) => ({ ...current, activeId: next.id, documents: [...current.documents, next] }));
            setRunnerOpen(false); setSelectedId(next.steps[0].id);
          }}><WorkflowIcon size={15} /><span>{t('workflowRun.' + label)}</span><PlusIcon size={14} /></button>)}
          {templates.map((template) =>
          <button key={template} type="button" disabled={running} onClick={() => addTemplate(template)} title={t(`calls.example_${template}_brief`)}>
            <PhoneCallIcon size={15} aria-hidden="true" /><span>{t(`calls.example_${template}`)}</span><PlusIcon size={14} aria-hidden="true" />
          </button>,
        )}</div>
        <div className="workflow-panel-heading workflow-panel-heading--steps">{t('workflows.add_step')}</div>
        <div className="workflow-palette">{STEP_KINDS.map((kind) => {
          const Icon = icons[kind];
          return <button key={kind} type="button" aria-label={t(`workflows.step_${kind}`)} disabled={running} onClick={() => addStep(kind)}><span className={`workflow-palette-icon workflow-palette-icon--${kind}`}><Icon size={16} aria-hidden="true" /></span><span>{t(`workflows.step_${kind}`)}{['agent', 'call'].includes(kind) && <small className="block text-[10px] text-muted-foreground">{t('workflows.local_draft')}</small>}</span><PlusIcon size={14} className="workflow-palette-add" aria-hidden="true" /></button>;
        })}</div>
      </aside>
      <section className="workflow-stage" aria-label={t('workflows.canvas')}>
        <div className="workflow-stage-toolbar"><label htmlFor="workflow-name">{t('workflows.name')}</label><Input id="workflow-name" value={document.name} disabled={running} onChange={(event) => updateDocument((current) => ({ ...current, name: event.target.value, autoNamed: false, updatedAt: Date.now() }))} /></div>
        <ReactFlow
          key={document.id}
          nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={(changes) => {
            onNodesChange(changes);
            const positions = new Map(changes.flatMap((change) => change.type === 'position' && change.position ? [[change.id, change.position] as const] : []));
            if (positions.size) updateDocument((current) => ({ ...current, updatedAt: Date.now(), steps: current.steps.map((step) => positions.has(step.id) ? { ...step, position: positions.get(step.id)! } : step) }));
          }}
          nodesDraggable={!running} nodesConnectable={!running} elementsSelectable={!running}
          onConnect={(connection: Connection) => updateDocument((current) => connectWorkflowSteps(current, connection.source, connection.target, connection.sourceHandle ?? undefined))}
          onNodeClick={(_, node) => { if (!running) { setRunnerOpen(false); setSelectedId(node.id); } }} onPaneClick={() => setSelectedId(null)}
          onNodeDragStop={(_, node) => updateDocument((current) => ({ ...current, updatedAt: Date.now(), steps: current.steps.map((step) => step.id === node.id ? { ...step, position: node.position } : step) }))}
          onNodesDelete={(deleted) => { for (const node of deleted) updateDocument((current) => removeWorkflowStep(current, node.id)); setSelectedId(null); }}
          onEdgesDelete={(deleted) => updateDocument((current) => ({ ...current, updatedAt: Date.now(), connections: current.connections.filter((edge) => !deleted.some((item) => item.id === edge.id)) }))}
          defaultViewport={{ x: 20, y: 74, zoom: 0.95 }} minZoom={0.2} maxZoom={1.8}
          deleteKeyCode={running ? null : ['Backspace', 'Delete']}
        >
          <Background variant={BackgroundVariant.Dots} color="var(--muted-foreground)" gap={20} size={1.2} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeColor="var(--muted-foreground)" maskColor="color-mix(in srgb, var(--background) 58%, transparent)" />
        </ReactFlow>
      </section>
      {runnerOpen && <WorkflowRunner key={document.id} document={document} storageReady={!library.cleanup?.length && !cleanupError} onBusy={setRunning} onClose={() => setRunnerOpen(false)} />}
      {!runnerOpen && selected && <aside className="workflow-inspector studio-scrollbar" aria-label={t('workflows.inspector')}>
        <div className="workflow-panel-heading">{t('workflows.inspector')}</div>
        <div className="workflow-inspector__content">
          <p className="workflow-eyebrow">{t(`workflows.step_${selected.kind}`)}</p>
          <label htmlFor="workflow-step-title">{t('workflows.step_name')}</label>
          <Input id="workflow-step-title" value={selected.title} placeholder={t(`workflows.step_${selected.kind}`)} onChange={(event) => editStep({ title: event.target.value })} />
          {selected.kind === 'call' && <><label htmlFor="workflow-step-phone">{t('calls.number_label')}</label><Input id="workflow-step-phone" value={selected.phone} onChange={(event) => editStep({ phone: event.target.value })} /></>}
          {!['start', 'end', 'speak', 'normalize', 'audio', 'transcribe', 'translate', 'convert', 'condition'].includes(selected.kind) && <><label htmlFor="workflow-step-text">{t(selected.kind === 'call' ? 'calls.brief_label' : 'workflows.instructions')}</label><Textarea id="workflow-step-text" value={selected.text} onChange={(event) => editStep({ text: event.target.value })} rows={5} /></>}
          <WorkflowInputs key={selected.id} step={selected} onChange={editStep} />
          {selected.kind === 'call' && <Button onClick={() => onCalls(selected)}><PhoneCallIcon aria-hidden="true" />{t('workflows.prepare_call')}</Button>}
          <Button variant="ghost" onClick={() => { updateDocument((current) => removeWorkflowStep(current, selected.id)); setSelectedId(null); }}><Trash2Icon aria-hidden="true" />{t('workflows.remove_step')}</Button>
        </div>
      </aside>}
    </div>
    <Dialog open={deletingId !== null} onOpenChange={(open) => { if (!open) setDeletingId(null); }}>
      <DialogContent showCloseButton={false} initialFocus={cancelDelete} style={{ background: 'var(--popover)' }}>
        <DialogHeader>
          <DialogTitle>{t('workflows.delete')}</DialogTitle>
          <DialogDescription>{t('workflows.delete_confirm', { name: library.documents.find((item) => item.id === deletingId)?.name ?? '' })}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          {deleteError && <p role="alert" className="text-xs text-destructive">{deleteError}</p>}
          <Button ref={cancelDelete} variant="outline" onClick={() => setDeletingId(null)}>{t('common.cancel')}</Button>
          <Button variant="destructive" disabled={deleteBusy} onClick={() => void deleteWorkflow()}><Trash2Icon aria-hidden="true" />{t('common.delete')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}

export function WorkflowsPage() {
  const [mode, setMode] = useState<'canvas' | 'calls'>('canvas');
  const [hasOpenedCalls, setHasOpenedCalls] = useState(false);
  const [callDraft, setCallDraft] = useState<{ to: string; brief: string } | undefined>();
  return <>
    <div className={mode === 'canvas' ? 'h-full min-h-0' : 'hidden'}>
      <ReactFlowProvider><WorkflowCanvas onCalls={(call) => {
        if (call) setCallDraft({ to: call.phone, brief: call.text });
        setHasOpenedCalls(true);
        setMode('calls');
      }} /></ReactFlowProvider>
    </div>
    {hasOpenedCalls && <div className={mode === 'calls' ? 'h-full min-h-0' : 'hidden'}>
      <CallsPage initialDraft={callDraft} onCanvas={() => setMode('canvas')} />
    </div>}
  </>;
}
