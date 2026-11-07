import { afterEach, expect, it, vi } from 'vitest';
import { installRandomUUID } from '../../lib/random-uuid';
import {
  addWorkflowStep,
  connectWorkflowSteps,
  duplicateWorkflowName,
  makeCallWorkflow,
  makeWorkflow,
  parseWorkflowLibrary,
  repairWorkflowName,
  removeWorkflowStep,
  removedWorkflowData,
} from './workflow-model';

afterEach(() => vi.unstubAllGlobals());

it('creates unique workflow ids when randomUUID is unavailable on a LAN origin', () => {
  let counter = 0;
  vi.stubGlobal('crypto', {
    getRandomValues: (bytes: Uint8Array) => {
      bytes.fill(0);
      bytes[0] = ++counter;
      return bytes;
    },
  });
  installRandomUUID();

  const document = makeWorkflow('Reception');
  const ids = [
    document.id,
    ...document.steps.map((step) => step.id),
    ...document.connections.map((connection) => connection.id),
  ];
  expect(new Set(ids).size).toBe(ids.length);
  expect(document.id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
});

it('creates a connected, editable call template without a destination number', () => {
  const document = makeCallWorkflow('Book a restaurant', 'Book a table for two');
  expect(document.steps.map((step) => step.kind)).toEqual(['start', 'call', 'end']);
  expect(document.steps[1].text).toBe('Book a table for two');
  expect(document.steps[1].phone).toBe('');
  expect(document.connections.map((edge) => [edge.source, edge.target])).toEqual([
    [document.steps[0].id, document.steps[1].id],
    [document.steps[1].id, document.steps[2].id],
  ]);
});

it('keeps a usable workflow after reopening saved canvas data', () => {
  const document = addWorkflowStep(makeWorkflow('Reception'), 'call');
  const linked = connectWorkflowSteps(document, document.steps[1].id, document.steps[3].id);
  const saved = JSON.stringify({ version: 1, activeId: linked.id, documents: [linked] });
  const loaded = parseWorkflowLibrary(saved, 'Untitled');
  expect(loaded.activeId).toBe(linked.id);
  expect(loaded.documents[0].steps).toHaveLength(4);
  expect(loaded.documents[0].connections).toHaveLength(3);
});

it('ignores broken saved edges and removes a step with all connected edges', () => {
  const document = makeWorkflow('Reception');
  const saved = JSON.stringify({
    version: 1,
    activeId: document.id,
    documents: [{
      ...document,
      connections: [
        ...document.connections,
        { id: 'broken', source: document.steps[0].id, target: 'missing' },
      ],
    }],
  });
  const loaded = parseWorkflowLibrary(saved, 'Untitled').documents[0];
  expect(loaded.connections).toHaveLength(2);
  const withoutAgent = removeWorkflowStep(loaded, document.steps[1].id);
  expect(withoutAgent.steps).toHaveLength(2);
  expect(withoutAgent.connections).toHaveLength(0);
  expect(connectWorkflowSteps(loaded, document.steps[0].id, document.steps[0].id)).toBe(loaded);
});

it('repairs a saved untranslated default name without changing custom names', () => {
  const broken = makeWorkflow('workflows.untitled');
  const custom = makeWorkflow('Reception');
  const saved = JSON.stringify({
    version: 1,
    activeId: broken.id,
    documents: [broken, custom],
  });
  const loaded = parseWorkflowLibrary(saved, 'Untitled workflow');
  expect(loaded.documents.map((document) => document.name)).toEqual([
    'Untitled workflow',
    'Reception',
  ]);
});

it('localizes an automatically named workflow when the app language changes', () => {
  const autoNamed = makeWorkflow('Untitled workflow', true);
  const custom = makeWorkflow('Reception');
  const saved = JSON.stringify({
    version: 1,
    activeId: autoNamed.id,
    documents: [autoNamed, custom],
  });
  const loaded = parseWorkflowLibrary(saved, 'Unbenannter Workflow');
  expect(loaded.documents.map((document) => document.name)).toEqual([
    'Unbenannter Workflow',
    'Reception',
  ]);
});

it('repairs copied legacy keys and numbers new copies without compounding names', () => {
  expect(repairWorkflowName('workflows.untitled copy copy copy', 'Untitled workflow')).toBe(
    'Untitled workflow (4)',
  );
  const documents = [
    makeWorkflow('Untitled workflow'),
    makeWorkflow('Untitled workflow (2)'),
    makeWorkflow('Untitled workflow (3)'),
  ];
  expect(duplicateWorkflowName('Untitled workflow (3)', documents)).toBe('Untitled workflow (4)');
});

it('preserves libraries and graphs beyond the former silent truncation limits', () => {
  const documents = Array.from({ length: 105 }, (_, i) => makeWorkflow(`Workflow ${i}`));
  documents[104].steps = Array.from({ length: 305 }, () => makeWorkflow('x').steps[0]);
  documents[104].connections = Array.from({ length: 605 }, (_, i) => ({ id: String(i), source: documents[104].steps[0].id, target: documents[104].steps[1].id }));
  const loaded = parseWorkflowLibrary(JSON.stringify({ version: 1, activeId: documents[104].id, documents }), 'Untitled');
  expect(loaded.documents).toHaveLength(105);
  expect(loaded.documents[104].steps).toHaveLength(305);
  expect(loaded.documents[104].connections).toHaveLength(605);
  expect(loaded.activeId).toBe(documents[104].id);
});

it('deletes media only after its last shared reference and invalidates affected runs', () => {
  const original = makeWorkflow('Original');
  original.steps[0].media = [{ id: 'source', name: 'source.wav', type: 'audio/wav', size: 10 }];
  const copy = { ...structuredClone(original), id: 'copy' };
  const before = { version: 1 as const, activeId: original.id, documents: [original, copy] };
  const one = { ...before, documents: [copy] };
  expect(removedWorkflowData(before, one)).toEqual({ media: [], runs: [original.id] });
  const noMedia = { ...one, documents: [{ ...copy, steps: [] }] };
  expect(removedWorkflowData(one, noMedia)).toEqual({ media: ['source'], runs: ['copy'] });
});

it('retains queued media deletions across reloads and ignores malformed queue entries', () => {
  const document = makeWorkflow('Remaining');
  const queued = { media: ['deleted-source'], runs: ['deleted-workflow'] };
  const loaded = parseWorkflowLibrary(JSON.stringify({ version: 1, activeId: document.id, documents: [document], cleanup: [queued, null, { media: [42], runs: [] }] }), 'Untitled');
  expect(loaded.cleanup).toEqual([queued]);
});
