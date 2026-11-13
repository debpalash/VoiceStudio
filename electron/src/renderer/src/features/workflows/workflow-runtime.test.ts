import { describe, expect, it, vi } from 'vitest';
import { makeSpeechWorkflow, makeProcessingWorkflow, makeCallWorkflow, parseWorkflowLibrary } from './workflow-model';
import { compileWorkflow, executeWorkflow, prepareRun, outputName } from './workflow-runtime';

function recipe(cleanup = false) {
  const document = makeSpeechWorkflow('Lessons', 'First lesson\n---\nSecond lesson', cleanup);
  document.steps[1].voiceId = 'saved-voice';
  return document;
}
const wav = () => new Blob(['wave'], { type: 'audio/wav' });

describe('workflow execution', () => {
  it('rejects unsupported nodes, disconnected steps, branches, cycles, missing voices and empty scripts before execution', () => {
    expect(() => compileWorkflow(makeCallWorkflow('Call', 'Hello'))).toThrow('unsupported');
    const document = recipe();
    document.connections.push({ id: 'cycle', source: document.steps[1].id, target: document.steps[0].id });
    expect(() => compileWorkflow(document)).toThrow('graph');
    const missing = recipe(); missing.connections.pop();
    expect(() => compileWorkflow(missing)).toThrow('graph');
    const noVoice = recipe(); noVoice.steps[1].voiceId = '';
    expect(() => compileWorkflow(noVoice)).toThrow('voice');
    const empty = recipe(); empty.steps[0].text = '';
    expect(() => compileWorkflow(empty)).toThrow('scripts');
  });
  it('executes scripts in graph order and checkpoints each audio output', async () => {
    const plan = compileWorkflow(recipe(true));
    const operations = { speak: vi.fn(async () => wav()), normalize: vi.fn(async () => wav()) };
    const save = vi.fn(async () => {});
    const result = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, save);
    expect(result.items.map((item) => item.state)).toEqual(['done', 'done']);
    expect(operations.speak.mock.calls).toHaveLength(2);
    expect(operations.normalize.mock.calls).toHaveLength(2);
    expect(Object.keys(result.items[0].audio)).toEqual(plan.steps.slice(1, -1).map((step) => step.id));
    expect(save.mock.calls.length).toBeGreaterThan(4);
  });
  it('does not expose an input as output when the first operation fails', async () => {
    const plan = compileWorkflow(recipe());
    const operations = { speak: vi.fn(async () => { throw new Error('offline'); }), normalize: vi.fn() };
    const result = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, async () => {});
    expect(result.items[0].state).toBe('failed');
    expect(result.items[0].outputStep).toBeUndefined();
  });
  it('retries failed cleanup without regenerating completed speech', async () => {
    const plan = compileWorkflow(recipe(true));
    const operations = { speak: vi.fn(async () => wav()), normalize: vi.fn(async () => { throw new Error('cleanup unavailable'); }) };
    const failed = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, async () => {});
    expect(failed.items.map((item) => item.state)).toEqual(['failed', 'ready']);
    const normalize = vi.fn(async () => wav());
    const result = await executeWorkflow(plan, prepareRun(plan, failed), { ...operations, normalize }, new AbortController().signal, async () => {});
    expect(result.items.every((item) => item.state === 'done')).toBe(true);
    expect(operations.speak).toHaveBeenCalledTimes(2);
  });
  it('stops on cancellation and preserves successful work for resume', async () => {
    const plan = compileWorkflow(recipe());
    const abort = new AbortController();
    const speak = vi.fn(async () => { abort.abort(); return wav(); });
    const result = await executeWorkflow(plan, prepareRun(plan), { speak, normalize: vi.fn() }, abort.signal, async () => {});
    expect(result.items.map((item) => item.state)).toEqual(['cancelled', 'ready']);
    expect(speak).toHaveBeenCalledTimes(1);
    expect(Object.keys(result.items[0].audio)).toHaveLength(1);
    const resumeSpeak = vi.fn(async () => wav());
    const resumed = await executeWorkflow(plan, prepareRun(plan, result), { speak: resumeSpeak, normalize: vi.fn() }, new AbortController().signal, async () => {});
    expect(resumeSpeak).toHaveBeenCalledTimes(1);
    expect(resumed.items.every((item) => item.state === 'done')).toBe(true);
  });
  it('invalidates outputs when inputs change, but not when nodes move or the workflow is renamed', () => {
    const document = recipe();
    const plan = compileWorkflow(document);
    const run = prepareRun(plan); run.items[0].state = 'done';
    document.name = 'Renamed'; document.steps[0].position.x = 999;
    expect(prepareRun(compileWorkflow(document), run).items[0].state).toBe('done');
    document.steps[1].speed = 1.2;
    expect(prepareRun(compileWorkflow(document), run).items[0].state).toBe('ready');
  });
  it('does not start inference when checkpoints cannot be saved', async () => {
    const plan = compileWorkflow(recipe());
    const speak = vi.fn();
    await expect(executeWorkflow(plan, prepareRun(plan), { speak, normalize: vi.fn() }, new AbortController().signal,
      async () => { throw new Error('quota'); })).rejects.toThrow('quota');
    expect(speak).not.toHaveBeenCalled();
  });
  it('round-trips executable settings and scripts through saved canvas data', () => {
    const document = recipe(true);
    document.steps[0].scripts = [{ name: 'Lesson', text: 'Welcome' }];
    const loaded = parseWorkflowLibrary(JSON.stringify({ version: 1, activeId: document.id, documents: [document] }), 'Untitled');
    expect(compileWorkflow(loaded.documents[0]).scripts).toEqual(document.steps[0].scripts);
    expect(compileWorkflow(loaded.documents[0]).signature).toBe(compileWorkflow(document).signature);
    expect(loaded.documents[0].steps[1].voiceId).toBe('saved-voice');
  });
  it('names exports safely on Windows, macOS and Linux', () => {
    expect(outputName('../course: one', 'lesson/one?', 0)).toBe('.._course_ one-01-lesson_one_.wav');
  });
});

describe('typed media workflows', () => {
  function audioRecipe(kinds: import('./workflow-model').StepKind[]) {
    const document = makeProcessingWorkflow('Audio recipe', kinds);
    document.steps[0].media = [{ id: 'source-1', name: 'interview.wav', type: 'audio/wav', size: 4 }];
    for (const step of document.steps) if (step.kind === 'speak' || step.kind === 'convert') step.voiceId = 'voice';
    return document;
  }

  it('transcribes, translates and narrates in order, persisting text and audio checkpoints', async () => {
    const document = audioRecipe(['audio', 'transcribe', 'translate', 'speak', 'end']);
    const plan = compileWorkflow(document);
    const source = wav();
    const transcribe = vi.fn(async () => 'Source transcript');
    const translate = vi.fn(async () => 'Translated transcript');
    const speak = vi.fn(async () => wav());
    const result = await executeWorkflow(plan, prepareRun(plan), {
      loadAudio: async () => source, transcribe, translate, speak, normalize: vi.fn(),
    }, new AbortController().signal, async () => {});
    expect(transcribe).toHaveBeenCalledWith(source, plan.steps[1], expect.any(AbortSignal));
    expect(translate).toHaveBeenCalledWith('Source transcript', plan.steps[2], expect.any(AbortSignal));
    expect(speak).toHaveBeenCalledWith('Translated transcript', plan.steps[3], expect.any(AbortSignal));
    expect(result.items[0].texts?.[plan.steps[2].id]).toBe('Translated transcript');
    expect(result.items[0].state).toBe('done');
    expect(result.items[0].outputStep).toBe(plan.steps[3].id);
  });

  it('resumes after translation failure without loading or transcribing audio again', async () => {
    const plan = compileWorkflow(audioRecipe(['audio', 'transcribe', 'translate', 'end']));
    const loadAudio = vi.fn(async () => wav());
    const transcribe = vi.fn(async () => 'Transcript');
    const operations = { loadAudio, transcribe, translate: vi.fn(async () => { throw new Error('missing language pack'); }), speak: vi.fn(), normalize: vi.fn() };
    const run = await executeWorkflow(plan, prepareRun(plan), operations, new AbortController().signal, async () => {});
    expect(run.items[0].state).toBe('failed');
    const resumed = await executeWorkflow(plan, prepareRun(plan, run), { ...operations, translate: async () => 'Translation' }, new AbortController().signal, async () => {});
    expect(resumed.items[0].state).toBe('done');
    expect(loadAudio).toHaveBeenCalledTimes(1);
    expect(transcribe).toHaveBeenCalledTimes(1);
    expect(resumed.items[0].texts?.[plan.steps[2].id]).toBe('Translation');
  });

  it('converts source audio with the selected voice and supports normalization afterwards', async () => {
    const plan = compileWorkflow(audioRecipe(['audio', 'convert', 'normalize', 'end']));
    const convert = vi.fn(async () => wav());
    const normalize = vi.fn(async () => wav());
    const result = await executeWorkflow(plan, prepareRun(plan), { loadAudio: async () => wav(), convert, normalize, speak: vi.fn() },
      new AbortController().signal, async () => {});
    expect(result.items[0].state).toBe('done');
    expect(convert).toHaveBeenCalledTimes(1);
    expect(normalize).toHaveBeenCalledTimes(1);
  });

  it('rejects incompatible artifacts and does not mark human recordings synthetic', () => {
    expect(() => compileWorkflow(audioRecipe(['audio', 'speak', 'end']))).toThrow('unsupported');
    expect(() => compileWorkflow(audioRecipe(['audio', 'normalize', 'end']))).toThrow('unsupported');
    expect(() => compileWorkflow(audioRecipe(['audio', 'translate', 'end']))).toThrow('unsupported');
    const missing = audioRecipe(['audio', 'transcribe', 'end']);
    missing.steps[0].media = [];
    expect(() => compileWorkflow(missing)).toThrow('media');
  });

  it('retains source references and typed settings when reopening drafts', () => {
    const document = audioRecipe(['audio', 'transcribe', 'translate', 'speak', 'end']);
    const saved = JSON.stringify({ version: 1, activeId: document.id, documents: [document] });
    const restored = parseWorkflowLibrary(saved, 'Untitled').documents[0];
    expect(compileWorkflow(restored).signature).toBe(compileWorkflow(document).signature);
  });
});
