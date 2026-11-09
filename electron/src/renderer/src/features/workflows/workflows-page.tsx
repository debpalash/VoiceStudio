import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from '@tanstack/react-router';
import { runRendererTask } from '@/lib/global-error-recovery';
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { LayoutTemplateIcon, PhoneCallIcon, PlusIcon, WorkflowIcon } from 'lucide-react';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { CallsPage } from '@/features/calls/calls-page';
import {
  STEP_KINDS,
  addWorkflowStep,
  addWorkflowStepAfter,
  connectWorkflowSteps,
  duplicateWorkflowName,
  freeOutput,
  makeWorkflow,
  removeWorkflowStep,
  stepSetupIssue,
  type StepKind,
  type WorkflowDocument,
  type WorkflowStep,
} from './workflow-model';
import './workflows.css';
import { useProfiles } from '@/hooks/use-profiles';
import { useWorkflowLibrary } from './use-workflow-library';
import { WorkflowDeleteDialog } from './workflow-delete-dialog';
import { WorkflowInspector } from './workflow-inspector';
import { WorkflowRunner } from './workflow-runner';
import { STEP_DRAG_TYPE, WorkflowStepPalette } from './workflow-step-palette';
import { nodeTypes, stepSummary, type CanvasNode } from './workflow-step-node';
import { WorkflowTemplatePicker } from './workflow-template-picker';
import { WorkflowTitleBar } from './workflow-title-bar';
import { FitWorkflowOnOpen } from './workflow-fit';

function WorkflowCanvas({
  onCalls,
}: {
  onCalls: (call?: Pick<WorkflowStep, 'phone' | 'text'>) => void;
}) {
  const { t } = useTranslation();
  const { untitled, library, setLibrary, cleanupError, retryCleanup } = useWorkflowLibrary();
  const [runnerOpen, setRunnerOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const profiles = useProfiles();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusToken, setFocusToken] = useState(0);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const flow = useReactFlow();
  const document =
    library.documents.find((item) => item.id === library.activeId) ?? library.documents[0];
  const selected = document.steps.find((step) => step.id === selectedId) ?? null;
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);

  const mappedNodes = useMemo<CanvasNode[]>(
    () =>
      document.steps.map((step) => ({
        id: step.id,
        type: 'workflowStep',
        position: step.position,
        selected: step.id === selectedId,
        data: {
          step,
          label: t(`workflows.step_${step.kind}`),
          issue: stepSetupIssue(step),
          summary: stepSummary(
            step,
            t,
            profiles.data?.find((profile) => profile.id === step.voiceId)?.name,
          ),
        },
      })),
    [document.steps, selectedId, t, profiles.data],
  );
  useEffect(() => setNodes(mappedNodes), [mappedNodes, setNodes]);

  const edges = useMemo<Edge[]>(
    () =>
      document.connections.map((edge) => ({
        ...edge,
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [document.connections],
  );
  const updateDocument = useCallback(
    (update: (current: WorkflowDocument) => WorkflowDocument) => {
      setLibrary((current) => ({
        ...current,
        documents: current.documents.map((item) =>
          item.id === current.activeId ? update(item) : item,
        ),
      }));
    },
    [setLibrary],
  );
  const editStep = (change: Partial<WorkflowStep>) => {
    if (!selectedId) return;
    setLibrary((library) => ({
      ...library,
      documents: library.documents.map((current) =>
        current.id !== document.id
          ? current
          : {
              ...current,
              updatedAt: Date.now(),
              steps: current.steps.map((step) =>
                step.id === selectedId ? { ...step, ...change } : step,
              ),
            },
      ),
    }));
  };
  const select = (id: string | null) => {
    setRunnerOpen(false);
    setSelectedId(id);
  };
  const canAppend = Boolean(selected && freeOutput(document, selected) !== null);
  const addStep = (kind: StepKind, position?: { x: number; y: number }) => {
    const next =
      !position && selected
        ? addWorkflowStepAfter(document, kind, selected.id)
        : addWorkflowStep(document, kind, position);
    updateDocument(() => next);
    select(next.steps[next.steps.length - 1].id);
  };
  const addWorkflow = () => {
    const next = makeWorkflow(untitled, true);
    setLibrary((current) => ({
      ...current,
      activeId: next.id,
      documents: [...current.documents, next],
    }));
    setSelectedId(null);
  };
  const addFromTemplate = (next: WorkflowDocument, selectIndex: number) => {
    setLibrary((current) => ({
      ...current,
      activeId: next.id,
      documents: [...current.documents, next],
    }));
    select(next.steps[selectIndex]?.id ?? null);
  };
  const blockedStep = document.steps.find((step) => stepSetupIssue(step));
  const blocked = blockedStep ? { step: blockedStep, issue: stepSetupIssue(blockedStep)! } : null;
  const duplicateWorkflow = () => {
    const copy: WorkflowDocument = {
      ...structuredClone(document),
      id: crypto.randomUUID(),
      name: duplicateWorkflowName(document.name, library.documents),
      autoNamed: false,
      updatedAt: Date.now(),
    };
    setLibrary((current) => ({
      ...current,
      activeId: copy.id,
      documents: [...current.documents, copy],
    }));
    setSelectedId(null);
  };
  const deleting = library.documents.find((item) => item.id === deletingId) ?? null;

  return (
    <div className="workflow-page">
      <WorkspaceHeader>
        <WorkflowIcon size={17} aria-hidden="true" />
        <h1 className="text-sm font-medium">{t('workflows.title')}</h1>
        <Button size="sm" variant="outline" disabled={running} onClick={() => onCalls()}>
          <PhoneCallIcon aria-hidden="true" />
          {t('calls.title')}
        </Button>
      </WorkspaceHeader>
      {cleanupError && (
        <div role="alert" className="flex items-center gap-2 px-4 text-xs text-destructive">
          {cleanupError}
          <Button size="sm" variant="ghost" onClick={() => void retryCleanup()}>
            {t('common.retry')}
          </Button>
        </div>
      )}
      <WorkflowTitleBar
        name={document.name}
        running={running}
        saved={!cleanupError}
        blocked={blocked}
        canDelete={library.documents.length > 1}
        onRename={(name) =>
          updateDocument((current) => ({
            ...current,
            name,
            autoNamed: false,
            updatedAt: Date.now(),
          }))
        }
        onRestoreName={() =>
          updateDocument((current) => ({ ...current, name: untitled, autoNamed: true }))
        }
        onSetUp={(step) => select(step.id)}
        onRun={() => {
          setSelectedId(null);
          setRunnerOpen(true);
        }}
        onDuplicate={duplicateWorkflow}
        onDelete={() => setDeletingId(document.id)}
      />
      <div className={`workflow-workspace${selected || runnerOpen ? ' has-selection' : ''}`}>
        <aside className="workflow-rail studio-scrollbar" aria-label={t('workflows.library')}>
          <div className="workflow-panel-heading">
            <span>{t('workflows.library')}</span>
            <Button
              size="icon-xs"
              variant="ghost"
              disabled={running}
              onClick={addWorkflow}
              aria-label={t('workflows.new')}
              title={t('workflows.new')}
            >
              <PlusIcon />
            </Button>
          </div>
          <div className="workflow-library-list">
            {library.documents.map((item) => (
              <button
                key={item.id}
                type="button"
                disabled={running}
                aria-current={item.id === document.id ? 'true' : undefined}
                className={`workflow-library-item${item.id === document.id ? ' is-active' : ''}`}
                onClick={() => {
                  setLibrary((current) => ({ ...current, activeId: item.id }));
                  setSelectedId(null);
                }}
              >
                <WorkflowIcon size={16} aria-hidden="true" />
                <span>{item.name}</span>
              </button>
            ))}
          </div>
          <div className="workflow-rail__footer">
            <Button
              size="sm"
              variant="ghost"
              className="w-full justify-start"
              disabled={running}
              onClick={() => setTemplatesOpen(true)}
            >
              <LayoutTemplateIcon aria-hidden="true" />
              {t('workflows.from_template')}
            </Button>
          </div>
        </aside>
        <section
          className="workflow-stage"
          aria-label={t('workflows.canvas')}
          aria-description={t('workflows.canvas_help')}
          onDragOver={(event) => {
            if (event.dataTransfer.types.includes(STEP_DRAG_TYPE)) {
              event.preventDefault();
              event.dataTransfer.dropEffect = 'copy';
            }
          }}
          onDrop={(event) => {
            const kind = event.dataTransfer.getData(STEP_DRAG_TYPE) as StepKind;
            if (running || !STEP_KINDS.includes(kind)) return;
            event.preventDefault();
            const point = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
            addStep(kind, { x: point.x - 100, y: point.y - 36 });
          }}
        >
          <WorkflowStepPalette
            disabled={running}
            after={canAppend}
            onAdd={(kind) => addStep(kind)}
          />
          <ReactFlow
            key={document.id}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={(changes) => {
              onNodesChange(changes);
              // Keyboard selection (Enter/Space on a focused step) opens its settings too.
              const picked = changes.find((change) => change.type === 'select' && change.selected);
              if (picked && picked.type === 'select' && !running && picked.id !== selectedId) {
                select(picked.id);
                setFocusToken((token) => token + 1);
              }
              const positions = new Map(
                changes.flatMap((change) =>
                  change.type === 'position' && change.position
                    ? [[change.id, change.position] as const]
                    : [],
                ),
              );
              if (positions.size)
                updateDocument((current) => ({
                  ...current,
                  updatedAt: Date.now(),
                  steps: current.steps.map((step) =>
                    positions.has(step.id) ? { ...step, position: positions.get(step.id)! } : step,
                  ),
                }));
            }}
            nodesDraggable={!running}
            nodesConnectable={!running}
            elementsSelectable={!running}
            onConnect={(connection: Connection) =>
              updateDocument((current) =>
                connectWorkflowSteps(
                  current,
                  connection.source,
                  connection.target,
                  connection.sourceHandle ?? undefined,
                ),
              )
            }
            onNodeClick={(_, node) => {
              if (!running) {
                select(node.id);
                setFocusToken(0);
              }
            }}
            onPaneClick={() => setSelectedId(null)}
            onNodeDragStop={(_, node) =>
              updateDocument((current) => ({
                ...current,
                updatedAt: Date.now(),
                steps: current.steps.map((step) =>
                  step.id === node.id ? { ...step, position: node.position } : step,
                ),
              }))
            }
            onNodesDelete={(deleted) => {
              for (const node of deleted)
                updateDocument((current) => removeWorkflowStep(current, node.id));
              setSelectedId(null);
            }}
            onEdgesDelete={(deleted) =>
              updateDocument((current) => ({
                ...current,
                updatedAt: Date.now(),
                connections: current.connections.filter(
                  (edge) => !deleted.some((item) => item.id === edge.id),
                ),
              }))
            }
            // Framed by FitWorkflowOnOpen once the steps are measured.
            defaultViewport={{ x: 64, y: 40, zoom: 0.95 }}
            minZoom={0.2}
            maxZoom={1.8}
            deleteKeyCode={running ? null : ['Backspace', 'Delete']}
          >
            <Background
              variant={BackgroundVariant.Dots}
              color="var(--muted-foreground)"
              gap={20}
              size={1.2}
            />
            <FitWorkflowOnOpen />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor="var(--muted-foreground)"
              maskColor="color-mix(in srgb, var(--background) 58%, transparent)"
            />
          </ReactFlow>
        </section>
        {runnerOpen && (
          <WorkflowRunner
            key={document.id}
            document={document}
            storageReady={!library.cleanup?.length && !cleanupError}
            onBusy={setRunning}
            onClose={() => setRunnerOpen(false)}
          />
        )}
        {!runnerOpen && selected && (
          <WorkflowInspector
            step={selected}
            focusToken={focusToken}
            onChange={editStep}
            onClose={() => setSelectedId(null)}
            onPrepareCall={() => onCalls(selected)}
            onRemove={() => {
              updateDocument((current) => removeWorkflowStep(current, selected.id));
              setSelectedId(null);
            }}
          />
        )}
      </div>
      <WorkflowTemplatePicker
        open={templatesOpen}
        onOpenChange={setTemplatesOpen}
        onPick={addFromTemplate}
      />
      <WorkflowDeleteDialog
        workflow={deleting}
        onCancel={() => setDeletingId(null)}
        onDeleted={(id) => {
          setLibrary((current) => {
            const documents = current.documents.filter((item) => item.id !== id);
            return documents.length
              ? { ...current, activeId: documents[0].id, documents }
              : current;
          });
          setSelectedId(null);
          setDeletingId(null);
        }}
      />
    </div>
  );
}

/** A call step's number and brief, carried from the canvas to the Calls view. */
let pendingCallDraft: { to: string; brief: string } | undefined;

export function WorkflowsPage() {
  const navigate = useNavigate();
  return (
    <div className="h-full min-h-0">
      <ReactFlowProvider>
        <WorkflowCanvas
          onCalls={(call) => {
            pendingCallDraft = call ? { to: call.phone, brief: call.text } : undefined;
            runRendererTask('Open Calls from the workflow canvas', () => navigate({ to: '/calls' }));
          }}
        />
      </ReactFlowProvider>
    </div>
  );
}

/** The Calls view at /calls, with a way back to the workflow canvas at /workflows. */
export function WorkflowCallsPage() {
  const navigate = useNavigate();
  const [draft] = useState(() => {
    const next = pendingCallDraft;
    pendingCallDraft = undefined;
    return next;
  });
  return (
    <div className="h-full min-h-0">
      <CallsPage initialDraft={draft} onCanvas={() =>
          runRendererTask('Return to the workflow canvas', () => navigate({ to: '/workflows' }))
        } />
    </div>
  );
}
