/**
 * Branching execution: the `condition` step.
 *
 * The canvas has always drawn a condition with two source handles, and the
 * palette offered it as a local draft, but `compileWorkflow` refused anything
 * that forked. These tests hold the executable version to the properties that
 * make a fork safe: one clip's route never decides another's, a re-merge may
 * not hand a shared tail two different kinds of value, and a draft that cannot
 * describe a complete route is rejected before any inference runs.
 */
import { describe, expect, it, vi } from 'vitest';
import { makeStep, type WorkflowConnection, type WorkflowDocument, type WorkflowStep } from './workflow-model';
import { compileWorkflow, executeWorkflow, matchesCondition, prepareRun } from './workflow-runtime';

const at = (x: number) => ({ x, y: 0 });
const wav = () => new Blob(['wave'], { type: 'audio/wav' });
const link = (source: WorkflowStep, target: WorkflowStep, sourceHandle?: string): WorkflowConnection =>
  ({ id: `${source.id}>${target.id}>${sourceHandle ?? ''}`, source: source.id, target: target.id, sourceHandle });
const graph = (steps: WorkflowStep[], connections: WorkflowConnection[]): WorkflowDocument =>
  ({ id: 'doc', name: 'Routing', updatedAt: 0, steps, connections });

/** start → condition ─yes→ speak(urgent voice) → end ; ─no→ speak(calm voice) → end */
function forked(phrase = 'urgent', text = 'Urgent: the roof is on fire\n---\nA quiet good morning') {
  const start = { ...makeStep('start', at(0)), text };
  const condition = { ...makeStep('condition', at(1)), text: phrase };
  const yes = { ...makeStep('speak', at(2)), voiceId: 'alarm' };
  const no = { ...makeStep('speak', at(3)), voiceId: 'calm' };
  const end = makeStep('end', at(4));
  return {
    document: graph([start, condition, yes, no, end], [
      link(start, condition), link(condition, yes, 'yes'), link(condition, no, 'no'),
      link(yes, end), link(no, end),
    ]),
    start, condition, yes, no, end,
  };
}

const speakingOps = () => {
  const voices: string[] = [];
  return {
    voices,
    operations: {
      speak: vi.fn(async (_text: string, step: WorkflowStep) => { voices.push(step.voiceId!); return wav(); }),
      normalize: vi.fn(async () => wav()),
    },
  };
};

describe('a fork routes each clip on its own', () => {
  it('keeps input text as exportable output on a branch directly to End', async () => {
    const { document, start, condition, end } = forked('urgent', 'Calm input');
    document.steps = [start, condition, end];
    document.connections = [link(start, condition), link(condition, end, 'yes'),
      link(condition, end, 'no')];
    const plan = compileWorkflow(document);
    const run = await executeWorkflow(plan, prepareRun(plan), speakingOps().operations,
      new AbortController().signal, async () => {});
    expect(run.items[0].state).toBe('done');
    expect(run.items[0].texts?.[run.items[0].outputStep!]).toBe('Calm input');
  });

  it('sends one clip down each branch in a single run', async () => {
    const plan = compileWorkflow(forked().document);
    const { voices, operations } = speakingOps();
    const run = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, async () => {});
    expect(run.items.map((item) => item.state)).toEqual(['done', 'done']);
    // The phrase decides per clip, so one run legitimately uses two voices.
    expect(voices).toEqual(['alarm', 'calm']);
  });

  it('never runs the branch it did not take', async () => {
    const { document, no } = forked('urgent', 'Urgent only');
    const plan = compileWorkflow(document);
    const { operations } = speakingOps();
    const run = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, async () => {});
    expect(operations.speak).toHaveBeenCalledTimes(1);
    // Nothing is cached for a step the clip never visited.
    expect(run.items[0].audio[no.id]).toBeUndefined();
  });

  it('records the step that actually produced the output', async () => {
    const { document, yes } = forked('urgent', 'Urgent only');
    const plan = compileWorkflow(document);
    const run = await executeWorkflow(plan, prepareRun(plan), speakingOps().operations, new AbortController().signal, async () => {});
    expect(run.items[0].outputStep).toBe(yes.id);
  });

  it('resumes onto the same branch instead of re-deciding blind', async () => {
    const { document } = forked();
    const plan = compileWorkflow(document);
    const first = speakingOps();
    const abort = new AbortController();
    first.operations.speak.mockImplementationOnce(async (_text: string, step: WorkflowStep) => {
      first.voices.push(step.voiceId!); abort.abort(); return wav();
    });
    const stopped = await executeWorkflow(plan, prepareRun(plan), first.operations, abort.signal, async () => {});
    expect(stopped.items.map((item) => item.state)).toEqual(['cancelled', 'ready']);
    const second = speakingOps();
    const resumed = await executeWorkflow(plan, prepareRun(plan, stopped), second.operations, new AbortController().signal, async () => {});
    expect(resumed.items.every((item) => item.state === 'done')).toBe(true);
    // The first clip's speech was already cached, so only the second is spoken.
    expect(second.voices).toEqual(['calm']);
  });
});

describe('branches that come back together', () => {
  it('runs a shared tail once per clip, whichever way it arrived', async () => {
    const { document, yes, no } = forked();
    const tail = makeStep('normalize', at(5));
    const end = document.steps.at(-1)!;
    document.steps.splice(4, 0, tail);
    document.connections = document.connections
      .filter((edge) => edge.target !== end.id)
      .concat([link(yes, tail), link(no, tail), link(tail, end)]);
    const plan = compileWorkflow(document);
    const { operations } = speakingOps();
    const run = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, async () => {});
    expect(run.items.every((item) => item.state === 'done')).toBe(true);
    expect(operations.normalize).toHaveBeenCalledTimes(2);
    expect(run.items.every((item) => item.outputStep === tail.id)).toBe(true);
  });

  it('refuses a merge whose branches carry different kinds of value', () => {
    // yes speaks (audio), no goes straight through (text). The shared end would
    // receive one or the other depending on the clip — the #1612 shape of bug,
    // caught at compile instead of at the join.
    const { document, condition, no, end } = forked();
    document.steps = document.steps.filter((step) => step.id !== no.id);
    document.connections = document.connections
      .filter((edge) => edge.source !== no.id && edge.target !== no.id)
      .concat([link(condition, end, 'no')]);
    expect(() => compileWorkflow(document)).toThrow('unsupported');
  });
});

describe('drafts a fork cannot describe', () => {
  it('rejects a condition with no phrase to test', () => {
    expect(() => compileWorkflow(forked('   ').document)).toThrow('condition');
  });

  it('rejects a condition reached with audio rather than text', () => {
    const { document, start, condition } = forked();
    const listen = { ...makeStep('audio', at(0)), media: [{ id: 'clip', name: 'a.wav', type: 'audio/wav', size: 10 }] };
    document.steps = [listen, ...document.steps.filter((step) => step.id !== start.id)];
    document.connections = document.connections
      .filter((edge) => edge.source !== start.id)
      .concat([link(listen, condition)]);
    expect(() => compileWorkflow(document)).toThrow('unsupported');
  });

  it.each([
    ['only one handle wired', (edges: WorkflowConnection[]) => edges.filter((edge) => edge.sourceHandle !== 'no')],
    ['both edges on one handle', (edges: WorkflowConnection[]) =>
      edges.map((edge) => edge.sourceHandle === 'no' ? { ...edge, sourceHandle: 'yes' } : edge)],
    ['an unlabelled fork', (edges: WorkflowConnection[]) =>
      edges.map((edge) => edge.sourceHandle ? { ...edge, sourceHandle: undefined } : edge)],
  ])('rejects %s', (_label, rewire) => {
    const { document } = forked();
    document.connections = rewire(document.connections);
    expect(() => compileWorkflow(document)).toThrow('graph');
  });

  it('still rejects a fork from a step that is not a condition', () => {
    const { document, yes, end } = forked();
    const extra = makeStep('end', at(6));
    document.steps.push(extra);
    document.connections.push(link(yes, extra));
    expect(end).toBeDefined();
    expect(() => compileWorkflow(document)).toThrow('graph');
  });

  it('rejects a branch that loops back into the route it came from', () => {
    const { document, condition, yes } = forked();
    document.connections.push(link(yes, condition));
    expect(() => compileWorkflow(document)).toThrow('graph');
  });

  it('rejects a branch that never reaches an end', () => {
    const { document, no, end } = forked();
    document.connections = document.connections.filter(
      (edge) => !(edge.source === no.id && edge.target === end.id));
    expect(() => compileWorkflow(document)).toThrow('graph');
  });
});

describe('the phrase test', () => {
  const step = (text: string, match?: WorkflowStep['match']) => ({ ...makeStep('condition', at(0)), text, match });

  it.each([
    ['contains', undefined, 'the word urgent here', true],
    ['contains', undefined, 'nothing to see', false],
    ['equals', 'equals' as const, 'urgent', true],
    ['equals', 'equals' as const, 'urgent now', false],
    ['starts', 'starts' as const, 'urgent now', true],
    ['starts', 'starts' as const, 'now urgent', false],
    ['ends', 'ends' as const, 'now urgent', true],
    ['ends', 'ends' as const, 'urgent now', false],
  ])('%s', (_label, match, value, expected) => {
    expect(matchesCondition(step('urgent', match), value)).toBe(expected);
  });

  it('ignores case and surrounding blanks on both sides', () => {
    expect(matchesCondition(step('  URGENT  '), 'this is Urgent')).toBe(true);
    expect(matchesCondition(step('urgent', 'equals'), '  URGENT\n')).toBe(true);
  });
});

describe('what invalidates a finished run', () => {
  // One document, mutated per case. Building a second `forked()` would mint new
  // step ids, so every signature would differ and each test would pass without
  // proving the thing it names.
  const base = forked();
  const baseline = compileWorkflow(base.document).signature;
  const variant = (change: (document: WorkflowDocument) => WorkflowDocument) =>
    compileWorkflow(change(structuredClone(base.document))).signature;
  const editCondition = (patch: Partial<WorkflowStep>) => (document: WorkflowDocument) => ({
    ...document,
    steps: document.steps.map((step) => step.id === base.condition.id ? { ...step, ...patch } : step),
  });

  it('changes when a branch is rewired, not only when a step is edited', () => {
    // Same steps, same settings — the yes and no destinations simply swap.
    expect(variant((document) => ({
      ...document,
      connections: document.connections.map((edge) => edge.source === base.condition.id
        ? { ...edge, target: edge.sourceHandle === 'yes' ? base.no.id : base.yes.id } : edge),
    }))).not.toBe(baseline);
  });

  it('changes when the phrase changes', () => {
    expect(variant(editCondition({ text: 'calm' }))).not.toBe(baseline);
  });

  it('changes when the match mode changes', () => {
    expect(variant(editCondition({ match: 'equals' }))).not.toBe(baseline);
  });

  it('is unchanged by where the nodes sit on the canvas', () => {
    expect(variant((document) => ({
      ...document,
      steps: document.steps.map((step) => ({ ...step, position: { x: step.position.x + 100, y: 40 } })),
    }))).toBe(baseline);
  });
});
