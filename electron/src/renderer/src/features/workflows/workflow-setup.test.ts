import { expect, it } from 'vitest';
import {
  addWorkflowStep,
  addWorkflowStepAfter,
  freeOutput,
  makeProcessingWorkflow,
  makeSpeechWorkflow,
  makeWorkflow,
  stepSetupIssue,
  type WorkflowDocument,
} from './workflow-model';
import { compileWorkflow, WorkflowValidationError } from './workflow-runtime';

const compileIssue = (document: WorkflowDocument) => {
  try {
    compileWorkflow(document);
    return null;
  } catch (error) {
    return error instanceof WorkflowValidationError ? error.code : 'other';
  }
};

it('flags every per-step setting compileWorkflow would reject', () => {
  const draft = makeSpeechWorkflow('Narration', '');
  expect(draft.steps.map(stepSetupIssue)).toEqual(['scripts', 'voice', null]);
  expect(compileIssue(draft)).toBe('voice');
  const voiced = {
    ...draft,
    steps: draft.steps.map((step) => (step.kind === 'speak' ? { ...step, voiceId: 'v1' } : step)),
  };
  expect(compileIssue(voiced)).toBe('scripts');
  const ready = {
    ...voiced,
    steps: voiced.steps.map((step) => (step.kind === 'start' ? { ...step, text: 'Hello' } : step)),
  };
  expect(ready.steps.map(stepSetupIssue)).toEqual([null, null, null]);
  expect(compileIssue(ready)).toBeNull();

  const local = makeProcessingWorkflow('Localize', [
    'audio',
    'transcribe',
    'translate',
    'speak',
    'end',
  ]);
  expect(local.steps.map(stepSetupIssue)).toEqual(['media', null, null, 'voice', null]);
  const translate = local.steps[2];
  expect(stepSetupIssue({ ...translate, language: 'Auto' })).toBe('language');
  expect(stepSetupIssue({ ...translate, sourceLanguage: '' })).toBe('language');
  const condition = addWorkflowStep(local, 'condition').steps.at(-1)!;
  expect(stepSetupIssue(condition)).toBe('condition');
  expect(stepSetupIssue({ ...condition, text: 'yes' })).toBeNull();
});

it('adds a step after the selected one and wires its free output', () => {
  const document = makeWorkflow('Draft');
  const [start, speak, end] = document.steps;
  // Speak already leads to End, so the new step is placed but not wired.
  const placed = addWorkflowStepAfter(document, 'normalize', speak.id);
  expect(placed.connections).toHaveLength(2);
  const added = placed.steps.at(-1)!;
  expect(added.position.x).toBe(speak.position.x + 275);
  expect(
    placed.steps.some(
      (step) =>
        step !== added &&
        step.position.x === added.position.x &&
        step.position.y === added.position.y,
    ),
  ).toBe(false);

  const unwired = {
    ...document,
    connections: document.connections.filter((edge) => edge.source !== speak.id),
  };
  const wired = addWorkflowStepAfter(unwired, 'normalize', speak.id);
  expect(wired.connections.at(-1)).toMatchObject({
    source: speak.id,
    target: wired.steps.at(-1)!.id,
    sourceHandle: undefined,
  });

  // End has no output; input steps never receive an edge.
  expect(addWorkflowStepAfter(document, 'speak', end.id).connections).toHaveLength(2);
  expect(addWorkflowStepAfter(unwired, 'audio', speak.id).connections).toHaveLength(1);
  expect(freeOutput(document, start)).toBeNull();

  // A condition fills its yes branch, then its no branch, then nothing.
  const branched = addWorkflowStep(document, 'condition');
  const condition = branched.steps.at(-1)!;
  const yes = addWorkflowStepAfter(branched, 'speak', condition.id);
  expect(yes.connections.at(-1)?.sourceHandle).toBe('yes');
  const no = addWorkflowStepAfter(yes, 'speak', condition.id);
  expect(no.connections.at(-1)?.sourceHandle).toBe('no');
  expect(freeOutput(no, condition)).toBeNull();
});
