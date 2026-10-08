import { afterEach, describe, expect, it, vi } from 'vitest';
import { BACKEND_GENERATE_BUDGET_S, generateAbortMs } from '@shared/utils/generateBudget';
import {
  generateBudgetPending,
  generateBudgetSettled,
  primeGenerateBudget,
  reportedGenerateBudget,
  resetGenerateBudgetForTests,
} from './generate-budget';

afterEach(() => {
  resetGenerateBudgetForTests();
  vi.unstubAllGlobals();
});

describe('generate budget', () => {
  it('outlasts budgets an operator raised on the backend', () => {
    const queueWait = BACKEND_GENERATE_BUDGET_S.queueWait + 3_600;
    expect(generateAbortMs(0, { queueWait })).toBe(generateAbortMs(0) + 3_600_000);
  });

  it('never drops below the built-in defaults or trusts malformed values', () => {
    expect(generateAbortMs(0, { queueWait: 1, modelLoad: 'x', executionBase: Number.NaN })).toBe(
      generateAbortMs(0),
    );
  });

  it('reads the backend report and keeps it when a later read fails', async () => {
    const body = {
      modelLoad: 5_000,
      queueWait: 1_800,
      executionBase: 900,
      progressExtensionCap: 1_800,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))));
    await primeGenerateBudget();
    expect(reportedGenerateBudget()).toEqual(body);

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await primeGenerateBudget();
    expect(reportedGenerateBudget()).toEqual(body);
  });
});

describe('generate budget ordering', () => {
  it('keeps the newest read when an older one answers last', async () => {
    let answerOld: (r: Response) => void = () => {};
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => new Promise<Response>((done) => (answerOld = done)))
      .mockResolvedValueOnce(new Response(JSON.stringify({ queueWait: 9_000 })));
    vi.stubGlobal('fetch', fetchMock);
    const older = primeGenerateBudget();
    await primeGenerateBudget();
    answerOld(new Response(JSON.stringify({ queueWait: 100 })));
    await older;
    expect(reportedGenerateBudget()).toEqual({ queueWait: 9_000 });
  });

  it('lets a generate wait for a read in flight, without starting one', async () => {
    expect(generateBudgetPending()).toBe(false);
    await generateBudgetSettled(10); // nothing in flight: returns at once
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((done) => (answer = done))),
    );
    const read = primeGenerateBudget();
    expect(generateBudgetPending()).toBe(true);
    const settled = generateBudgetSettled(5_000);
    answer(new Response(JSON.stringify({ modelLoad: 4_000 })));
    await settled;
    await read;
    expect(reportedGenerateBudget()).toEqual({ modelLoad: 4_000 });
    expect(generateBudgetPending()).toBe(false);
  });
});
