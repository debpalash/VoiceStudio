import { CONDITION_BRANCHES } from './workflow-model';
import type { ConditionBranch, WorkflowDocument, WorkflowStep } from './workflow-model';

export type RunState = 'ready' | 'running' | 'done' | 'failed' | 'cancelled';
export interface RunItem {
  name: string;
  text: string;
  state: RunState;
  stepId?: string;
  error?: string;
  audio: Record<string, Blob>;
  texts?: Record<string, string>;
  sourceId?: string;
  outputStep?: string;
}
export interface WorkflowRun {
  version: 1;
  signature: string;
  updatedAt: number;
  items: RunItem[];
}
export interface ExecutionPlan {
  /** Every reachable step, the input node first, in walk order. */
  steps: WorkflowStep[];
  scripts: { name: string; text: string; sourceId?: string }[];
  signature: string;
  /** The one successor of each step that has exactly one. */
  next: Record<string, string>;
  /** Each condition's two successors, by the handle the canvas draws. */
  branches: Record<string, Record<ConditionBranch, string>>;
}
export class WorkflowValidationError extends Error {
  constructor(readonly code: 'graph' | 'unsupported' | 'scripts' | 'voice' | 'media' | 'language' | 'condition') {
    super(code);
  }
}

type ValueType = 'text' | 'audio' | 'speech';

/**
 * Does the text reaching `step` satisfy it?
 *
 * Case-insensitive, and deliberately `toLowerCase` rather than
 * `toLocaleLowerCase`: the same draft has to take the same branch on every
 * machine, and the locale-aware form does not (Turkish dotless i lowercases
 * `I` to `ı`, so a Turkish host would miss a phrase a US host matches).
 */
export function matchesCondition(step: WorkflowStep, value: string): boolean {
  const phrase = step.text.trim().toLowerCase();
  const text = value.trim().toLowerCase();
  switch (step.match) {
    case 'equals': return text === phrase;
    case 'starts': return text.startsWith(phrase);
    case 'ends': return text.endsWith(phrase);
    default: return text.includes(phrase);
  }
}

/** Validate the entire graph before any side effect. Unsupported drafts never partially execute. */
export function compileWorkflow(document: WorkflowDocument, engine = ''): ExecutionPlan {
  const starts = document.steps.filter((step) => ['start', 'audio'].includes(step.kind));
  const ids = new Set(document.steps.map((step) => step.id));
  if (starts.length !== 1 || ids.size !== document.steps.length ||
    document.connections.some((edge) => !ids.has(edge.source) || !ids.has(edge.target)))
    throw new WorkflowValidationError('graph');
  const byId = new Map<string, WorkflowStep>(document.steps.map((step) => [step.id, step]));
  const leaving = (id: string) => document.connections.filter((edge) => edge.source === id);
  for (const step of document.steps) {
    // A branch may re-merge, so more than one edge IN is legitimate now. An
    // input node with any, or any other node with none, still is not.
    const entering = document.connections.filter((edge) => edge.target === step.id).length;
    if (['start', 'audio'].includes(step.kind) ? entering !== 0 : entering === 0)
      throw new WorkflowValidationError('graph');
  }
  const steps: WorkflowStep[] = [];
  const next: Record<string, string> = {};
  const branches: Record<string, Record<ConditionBranch, string>> = {};
  // The value kind each node is ENTERED with. Two paths that re-merge must
  // agree on it, or the shared tail would receive audio down one branch and
  // text down the other; recording it also stops the walk re-validating a
  // shared tail once per branch, which is what keeps this linear in graph size.
  const entered = new Map<string, ValueType>();
  const walk = (step: WorkflowStep, type: ValueType, path: ReadonlySet<string>) => {
    if (path.has(step.id)) throw new WorkflowValidationError('graph');
    const seen = entered.get(step.id);
    if (seen !== undefined) {
      if (seen !== type) throw new WorkflowValidationError('unsupported');
      return; // Already validated from here, with the same input kind.
    }
    entered.set(step.id, type);
    steps.push(step);
    const edges = leaving(step.id);
    if (step.kind === 'end') {
      if (edges.length) throw new WorkflowValidationError('graph');
      return;
    }
    const onward = new Set(path).add(step.id);
    if (step.kind === 'condition') {
      // Only a condition may fork, and only into exactly the two handles the
      // canvas draws: an unlabelled, duplicated or missing edge leaves a branch
      // with no defined destination, which must fail here rather than mid-run.
      if (type !== 'text') throw new WorkflowValidationError('unsupported');
      if (!step.text.trim()) throw new WorkflowValidationError('condition');
      if (edges.length !== 2 || CONDITION_BRANCHES.some(
        (branch) => edges.filter((edge) => edge.sourceHandle === branch).length !== 1))
        throw new WorkflowValidationError('graph');
      branches[step.id] = Object.fromEntries(CONDITION_BRANCHES.map((branch) =>
        [branch, edges.find((edge) => edge.sourceHandle === branch)!.target],
      )) as Record<ConditionBranch, string>;
      for (const branch of CONDITION_BRANCHES) walk(byId.get(branches[step.id][branch])!, type, onward);
      return;
    }
    if (edges.length !== 1 || edges[0].sourceHandle) throw new WorkflowValidationError('graph');
    // Explicit contracts prevent accidental audio→text coercion or marking human input synthetic.
    let forward: ValueType = type;
    if (['start', 'audio'].includes(step.kind)) {
      // The input node only says where the value came from; it transforms nothing.
    } else if (step.kind === 'speak' && type === 'text') forward = 'speech';
    else if (step.kind === 'convert' && type !== 'text') forward = 'speech';
    else if (step.kind === 'transcribe' && type !== 'text') forward = 'text';
    else if (step.kind === 'translate' && type === 'text') {
      if (!step.sourceLanguage || !step.language || step.language === 'Auto')
        throw new WorkflowValidationError('language');
    } else if (step.kind !== 'normalize' || type !== 'speech')
      throw new WorkflowValidationError('unsupported');
    if (['speak', 'convert'].includes(step.kind) && !step.voiceId?.trim())
      throw new WorkflowValidationError('voice');
    next[step.id] = edges[0].target;
    walk(byId.get(edges[0].target)!, forward, onward);
  };
  walk(starts[0], starts[0].kind === 'audio' ? 'audio' : 'text', new Set());
  // Every path ends at an `end` (a non-end node without exactly one edge out
  // already threw), so what is left to check is that nothing is stranded.
  if (steps.length !== document.steps.length) throw new WorkflowValidationError('graph');
  if (steps.length < 3) throw new WorkflowValidationError('unsupported');
  const scripts = steps[0].kind === 'audio'
    ? (steps[0].media || []).map((file) => ({ name: file.name.replace(/\.[^.]+$/, ''), text: '', sourceId: file.id }))
    : steps[0].scripts?.length ? steps[0].scripts : steps[0].text
      .split(/^\s*---\s*$/m).map((text, index) => ({ name: `${index + 1}`, text: text.trim() }));
  if (steps[0].kind === 'audio') {
    if (!scripts.length || scripts.length > 50) throw new WorkflowValidationError('media');
  } else if (!scripts.length || scripts.length > 50 || scripts.some((script) => !script.text.trim() || script.text.length > 20_000))
    throw new WorkflowValidationError('scripts');
  // Layout, names, and selection never invalidate generated audio; executable settings do.
  // Wiring counts as an executable setting now: with a fork in the graph, moving
  // an edge changes which steps a clip passes through without changing any of
  // them, and a run restored across that edit would show output from a path the
  // draft no longer describes. Sorted, so the edge list is a set rather than an
  // authoring order.
  const signature = JSON.stringify({ engine,
    steps: steps.map(({ id, kind, voiceId, language, speed, targetDb, sourceLanguage, provider, text, match }) => ['speak', 'convert'].includes(kind)
      ? { id, kind, voiceId, language: language || 'Auto', speed: speed ?? 1 } : kind === 'normalize' ? { id, kind, targetDb: targetDb ?? -2 } : kind === 'translate' ? { id, kind, sourceLanguage, language, provider: provider || 'argos' }
      : kind === 'condition' ? { id, kind, text, match: match || 'contains' }
      : kind === 'transcribe' ? { id, kind } : { id, kind }),
    edges: document.connections
      .filter((edge) => entered.has(edge.source))
      .map((edge) => [edge.source, edge.target, edge.sourceHandle ?? ''].join('>'))
      .sort(),
    scripts,
  });
  return { steps, scripts, signature, next, branches };
}

export function prepareRun(plan: ExecutionPlan, previous?: WorkflowRun | null): WorkflowRun {
  if (previous?.version === 1 && previous.signature === plan.signature && previous.items.length === plan.scripts.length) {
    const restored = structuredClone(previous);
    return { ...restored, items: restored.items.map((item) => ({ ...item, state: item.state === 'done' ? 'done' : 'ready', error: undefined })) };
  }
  return { version: 1, signature: plan.signature, updatedAt: Date.now(), items: plan.scripts.map((script) => ({ ...script, state: 'ready', audio: {}, texts: {} })) };
}

export interface WorkflowOperations {
  loadAudio?(id: string): Promise<Blob>;
  transcribe?(audio: Blob, step: WorkflowStep, signal: AbortSignal): Promise<string>;
  translate?(text: string, step: WorkflowStep, signal: AbortSignal): Promise<string>;
  convert?(audio: Blob, step: WorkflowStep, signal: AbortSignal): Promise<Blob>;
  speak(text: string, step: WorkflowStep, signal: AbortSignal): Promise<Blob>;
  normalize(audio: Blob, step: WorkflowStep, signal: AbortSignal): Promise<Blob>;
}

/** Serial execution avoids competing model loads. Every successful step is a durable checkpoint. */
export async function executeWorkflow(
  plan: ExecutionPlan, run: WorkflowRun, operations: WorkflowOperations,
  signal: AbortSignal, checkpoint: (run: WorkflowRun) => Promise<void>,
): Promise<WorkflowRun> {
  const save = async () => {
    run.updatedAt = Date.now();
    await checkpoint(structuredClone(run));
  };
  const byId = new Map<string, WorkflowStep>(plan.steps.map((step) => [step.id, step]));
  await save(); // Storage must work before doing expensive inference.
  for (const item of run.items) {
    if (item.state === 'done') continue;
    try {
      signal.throwIfAborted();
      item.texts ??= {};
      let value: string | Blob = item.text;
      const input = plan.steps[0];
      // Even a condition-only route must leave an exportable checkpoint.
      item.outputStep = input.id;
      if (!item.sourceId) item.texts[input.id] = item.text;
      if (item.sourceId) {
        value = item.audio[input.id] || await operations.loadAudio!(item.sourceId);
        item.audio[input.id] = value;
        await save();
      }
      // Each clip walks the graph itself: with a fork, two clips from one run
      // legitimately visit different steps. Conditions are re-read rather than
      // recorded, because the value they test is itself checkpointed — a resume
      // re-evaluates the same text and so takes the same branch.
      let finished = input;
      let cursor: string | undefined = plan.next[input.id];
      while (cursor) {
        // Annotated because `cursor`'s next value is read off this step, and
        // inferring one from the other is circular.
        const step: WorkflowStep = byId.get(cursor)!;
        if (step.kind === 'end') { finished = step; break; }
        if (step.kind === 'condition') {
          cursor = plan.branches[step.id][matchesCondition(step, value as string) ? 'yes' : 'no'];
          continue;
        }
        signal.throwIfAborted();
        item.stepId = step.id;
        item.state = 'running';
        await save();
        const cached = item.texts[step.id] ?? item.audio[step.id];
        if (cached !== undefined) value = cached;
        else {
          switch (step.kind) {
            case 'speak': value = await operations.speak(value as string, step, signal); break;
            case 'normalize': value = await operations.normalize(value as Blob, step, signal); break;
            case 'transcribe': value = await operations.transcribe!(value as Blob, step, signal); break;
            case 'translate': value = await operations.translate!(value as string, step, signal); break;
            case 'convert': value = await operations.convert!(value as Blob, step, signal); break;
            default: throw new WorkflowValidationError('unsupported');
          }
          if (typeof value === 'string') {
            if (!value.trim()) throw new Error('workflowRun.incomplete');
            item.texts[step.id] = value;
          } else {
            if (!value.size) throw new Error('workflowRun.incomplete');
            item.audio[step.id] = value;
          }
        }
        item.outputStep = step.id;
        await save();
        cursor = plan.next[step.id];
      }
      signal.throwIfAborted();
      item.state = 'done';
      item.stepId = finished.id;
      await save();
    } catch (error) {
      item.state = signal.aborted ? 'cancelled' : 'failed';
      item.error = signal.aborted ? undefined : error instanceof Error ? error.message : String(error);
      await save();
      break; // Explicit retry; never silently skip a failed clip.
    }
  }
  return run;
}

export function outputName(workflowName: string, itemName: string, index: number, extension: 'wav' | 'txt' = 'wav'): string {
  // Windows disallows control bytes in filenames; stripping them also prevents path surprises.
  // eslint-disable-next-line no-control-regex
  const safe = (value: string) => value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[. ]+$/g, '').slice(0, 70);
  return `${safe(workflowName) || 'workflow'}-${String(index + 1).padStart(2, '0')}-${safe(itemName) || 'audio'}.${extension}`;
}
