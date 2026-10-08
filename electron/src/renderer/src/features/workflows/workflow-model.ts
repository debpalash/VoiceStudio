export const WORKFLOW_STORAGE_KEY = 'voicestudio.workflows.v1';

export type StepKind = 'start' | 'agent' | 'speak' | 'condition' | 'call' | 'normalize' | 'audio' | 'transcribe' | 'translate' | 'convert' | 'end';
export const STEP_KINDS: StepKind[] = ['start', 'agent', 'speak', 'condition', 'call', 'normalize', 'audio', 'transcribe', 'translate', 'convert', 'end'];

export type ConditionMatch = 'contains' | 'equals' | 'starts' | 'ends';
export const CONDITION_MATCHES: ConditionMatch[] = ['contains', 'equals', 'starts', 'ends'];
/** The two source handles a condition offers; the canvas already renders them. */
export const CONDITION_BRANCHES = ['yes', 'no'] as const;
export type ConditionBranch = (typeof CONDITION_BRANCHES)[number];

export interface WorkflowStep {
  id: string;
  kind: StepKind;
  position: { x: number; y: number };
  title: string;
  text: string;
  phone: string;
  voiceId?: string;
  language?: string;
  speed?: number;
  targetDb?: number;
  sourceLanguage?: string;
  provider?: 'argos' | 'nllb';
  /** How a `condition` compares the text reaching it against its own `text`. */
  match?: ConditionMatch;
  media?: { id: string; name: string; type: string; size: number }[];
  scripts?: { name: string; text: string }[];
}

export interface WorkflowConnection {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
}

export interface WorkflowDocument {
  id: string;
  name: string;
  autoNamed?: boolean;
  updatedAt: number;
  steps: WorkflowStep[];
  connections: WorkflowConnection[];
}

export interface WorkflowLibrary {
  version: 1;
  activeId: string;
  documents: WorkflowDocument[];
  cleanup?: { media: string[]; runs: string[] }[];
}

export function repairWorkflowName(name: string, untitled: string): string {
  if (!/^(?:workflows\.untitled|Untitled workflow)(?: copy)*$/.test(name)) return name;
  const copies = name.match(/\bcopy\b/g)?.length ?? 0;
  return copies ? `${untitled} (${copies + 1})` : untitled;
}

export function duplicateWorkflowName(name: string, documents: WorkflowDocument[]): string {
  const base = name.replace(/ \(\d+\)$/, '');
  const names = new Set(documents.map((document) => document.name));
  let number = 2;
  while (names.has(`${base} (${number})`)) number += 1;
  return `${base} (${number})`;
}

export function makeStep(kind: StepKind, position: { x: number; y: number }): WorkflowStep {
  return { id: crypto.randomUUID(), kind, position, title: '', text: '', phone: '' };
}

export function makeWorkflow(name: string, autoNamed = false): WorkflowDocument {
  const start = makeStep('start', { x: 40, y: 80 });
  const agent = makeStep('speak', { x: 315, y: 80 });
  const end = makeStep('end', { x: 590, y: 80 });
  return {
    id: crypto.randomUUID(),
    name,
    autoNamed,
    updatedAt: Date.now(),
    steps: [start, agent, end],
    connections: [
      { id: crypto.randomUUID(), source: start.id, target: agent.id },
      { id: crypto.randomUUID(), source: agent.id, target: end.id },
    ],
  };
}

/** A ready-to-edit call draft; the graph itself never dials or executes steps. */
export function makeCallWorkflow(name: string, brief: string): WorkflowDocument {
  const document = makeWorkflow(name);
  return {
    ...document,
    steps: document.steps.map((step) => step.kind === 'speak'
      ? { ...step, kind: 'call' as const, title: name, text: brief }
      : step),
  };
}

/** Treat saved canvas data as untrusted; never let malformed storage break the workspace. */
export function parseWorkflowLibrary(raw: string | null, untitled: string): WorkflowLibrary {
  try {
    const value = JSON.parse(raw ?? 'null');
    if (value?.version === 1 && Array.isArray(value.documents)) {
      const documents: WorkflowDocument[] = value.documents.flatMap((candidate: unknown) => {
        if (!candidate || typeof candidate !== 'object') return [];
        const item = candidate as Record<string, unknown>;
        if (typeof item.id !== 'string' || typeof item.name !== 'string' || !Array.isArray(item.steps))
          return [];
        const steps = item.steps.flatMap((candidateStep: unknown): WorkflowStep[] => {
          if (!candidateStep || typeof candidateStep !== 'object') return [];
          const step = candidateStep as Record<string, unknown>;
          const position = step.position as Record<string, unknown> | undefined;
          if (
            typeof step.id !== 'string' ||
            !STEP_KINDS.includes(step.kind as StepKind) ||
            !position ||
            !Number.isFinite(position.x) ||
            !Number.isFinite(position.y)
          )
            return [];
          return [{
            id: step.id,
            kind: step.kind as StepKind,
            position: { x: position.x as number, y: position.y as number },
            title: typeof step.title === 'string' ? step.title.slice(0, 120) : '',
            text: typeof step.text === 'string' ? step.text.slice(0, 20_000) : '',
            voiceId: typeof step.voiceId === 'string' ? step.voiceId.slice(0, 200) : '',
            language: typeof step.language === 'string' ? step.language.slice(0, 80) : 'Auto',
            speed: typeof step.speed === 'number' && Number.isFinite(step.speed) ? Math.min(2, Math.max(0.5, step.speed)) : 1,
            sourceLanguage: typeof step.sourceLanguage === 'string' ? step.sourceLanguage.slice(0, 80) : '',
            provider: step.provider === 'nllb' ? 'nllb' : 'argos',
            match: CONDITION_MATCHES.includes(step.match as ConditionMatch)
              ? (step.match as ConditionMatch) : 'contains',
            media: Array.isArray(step.media) ? step.media.slice(0, 50).flatMap((entry) =>
              entry && typeof entry.id === 'string' && typeof entry.name === 'string' &&
              typeof entry.size === 'number' && entry.size > 0 && entry.size <= 64 * 1024 * 1024
                ? [{ id: entry.id.slice(0, 80), name: entry.name.slice(0, 120), type: typeof entry.type === 'string' ? entry.type.slice(0, 80) : '', size: entry.size }] : []) : [],
            targetDb: typeof step.targetDb === 'number' && Number.isFinite(step.targetDb) ? Math.min(-1, Math.max(-24, step.targetDb)) : -2,
            scripts: Array.isArray(step.scripts) ? step.scripts.slice(0, 50).flatMap((entry) => entry && typeof entry.name === 'string' && typeof entry.text === 'string' ? [{ name: entry.name.slice(0, 120), text: entry.text.slice(0, 20_000) }] : []) : [],
            phone: typeof step.phone === 'string' ? step.phone.slice(0, 80) : '',
          }];
        });
        const ids = new Set(steps.map((step) => step.id));
        const connections = (Array.isArray(item.connections) ? item.connections : [])
          .flatMap((candidateEdge: unknown): WorkflowConnection[] => {
            if (!candidateEdge || typeof candidateEdge !== 'object') return [];
            const edge = candidateEdge as Record<string, unknown>;
            if (
              typeof edge.id !== 'string' ||
              typeof edge.source !== 'string' ||
              typeof edge.target !== 'string' ||
              !ids.has(edge.source) ||
              !ids.has(edge.target) ||
              edge.source === edge.target
            )
              return [];
            return [{
              id: edge.id,
              source: edge.source,
              target: edge.target,
              sourceHandle: typeof edge.sourceHandle === 'string' ? edge.sourceHandle : undefined,
            }];
          });
        return [{
          id: item.id,
          // Early builds could save a translation key, including in copy names.
          name: item.autoNamed === true
            ? untitled
            : repairWorkflowName(item.name.slice(0, 120), untitled) || untitled,
          autoNamed: item.autoNamed === true ||
            item.name === 'workflows.untitled' || item.name === 'Untitled workflow',
          updatedAt: typeof item.updatedAt === 'number' ? item.updatedAt : Date.now(),
          steps,
          connections,
        }];
      });
      if (documents.length) {
        return {
          version: 1,
          activeId: documents.some((document) => document.id === value.activeId)
            ? value.activeId
            : documents[0].id,
          documents,
          cleanup: Array.isArray(value.cleanup) ? value.cleanup.filter((task: { media?: unknown; runs?: unknown }) =>
            task && Array.isArray(task.media) && task.media.every((id) => typeof id === 'string') &&
            Array.isArray(task.runs) && task.runs.every((id) => typeof id === 'string')) : [],
        };
      }
    }
  } catch {
    // An unreadable draft must not prevent creating a new workflow.
  }
  const first = makeWorkflow(untitled, true);
  return { version: 1, activeId: first.id, documents: [first] };
}

export function addWorkflowStep(document: WorkflowDocument, kind: StepKind): WorkflowDocument {
  const index = document.steps.length;
  const step = makeStep(kind, {
    x: 40 + (index % 3) * 275,
    y: 80 + Math.floor(index / 3) * 155,
  });
  return { ...document, steps: [...document.steps, step], updatedAt: Date.now() };
}

export function connectWorkflowSteps(
  document: WorkflowDocument,
  source: string,
  target: string,
  sourceHandle?: string,
): WorkflowDocument {
  if (
    source === target ||
    !document.steps.some((step) => step.id === source) ||
    !document.steps.some((step) => step.id === target) ||
    document.connections.some(
      (edge) => edge.source === source && edge.target === target && edge.sourceHandle === sourceHandle,
    )
  )
    return document;
  return {
    ...document,
    updatedAt: Date.now(),
    connections: [...document.connections, { id: crypto.randomUUID(), source, target, sourceHandle }],
  };
}

export function removeWorkflowStep(document: WorkflowDocument, id: string): WorkflowDocument {
  return {
    ...document,
    updatedAt: Date.now(),
    steps: document.steps.filter((step) => step.id !== id),
    connections: document.connections.filter((edge) => edge.source !== id && edge.target !== id),
  };
}

export function makeSpeechWorkflow(name: string, script: string, cleanup = false): WorkflowDocument {
  const document = makeWorkflow(name);
  const steps = [
    { ...document.steps[0], text: script },
    { ...document.steps[1], kind: 'speak' as const, voiceId: '', language: 'Auto', speed: 1 },
    ...(cleanup ? [makeStep('normalize', { x: 315, y: 250 })] : []),
    { ...document.steps[2], position: { x: cleanup ? 40 : 590, y: cleanup ? 250 : 80 } },
  ];
  return { ...document, steps, connections: steps.slice(1).map((step, index) => ({
    id: crypto.randomUUID(), source: steps[index].id, target: step.id,
  })) };
}

export function makeProcessingWorkflow(name: string, kinds: StepKind[]): WorkflowDocument {
  const steps = kinds.map((kind, index) => ({
    ...makeStep(kind, { x: 40 + (index % 2) * 290, y: 80 + Math.floor(index / 2) * 160 }),
    language: kind === 'translate' ? 'Spanish' : 'Auto',
    sourceLanguage: 'English',
    provider: 'argos' as const,
  }));
  return {
    id: crypto.randomUUID(), name, updatedAt: Date.now(), steps,
    connections: steps.slice(1).map((step, index) => ({
      id: crypto.randomUUID(), source: steps[index].id, target: step.id,
    })),
  };
}

/** Duplicated workflows can share source blobs; only the last reference releases one. */
export function removedWorkflowData(before: WorkflowLibrary, after: WorkflowLibrary) {
  const mediaIds = (document: WorkflowDocument) => document.steps.flatMap((step) => step.media?.map((file) => file.id) || []);
  const retained = new Set(after.documents.flatMap(mediaIds));
  return {
    media: [...new Set(before.documents.flatMap(mediaIds))].filter((id) => !retained.has(id)),
    runs: before.documents.filter((document) => {
      const next = after.documents.find((entry) => entry.id === document.id);
      return !next || mediaIds(document).some((id) => !mediaIds(next).includes(id));
    }).map((document) => document.id),
  };
}
