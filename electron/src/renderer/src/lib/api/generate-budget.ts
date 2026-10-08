import type { ReportedGenerateBudget } from '@shared/utils/generateBudget';
import { apiJson } from './client';

let reported: ReportedGenerateBudget = {};
let pending: Promise<void> | null = null;
let generation = 0;

/** The backend's active generate budgets, as last reported; empty until then. */
export function reportedGenerateBudget(): ReportedGenerateBudget {
  return reported;
}

/** Resolves once a read already in flight settles, or after `timeoutMs`. Never starts a read. */
export async function generateBudgetSettled(timeoutMs: number): Promise<void> {
  if (!pending) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  await Promise.race([pending, new Promise<void>((done) => (timer = setTimeout(done, timeoutMs)))]);
  clearTimeout(timer);
}

export function generateBudgetPending(): boolean {
  return pending !== null;
}

/**
 * Read GET /generate/budget so the /generate backstop follows timeouts an
 * operator raised through the environment. Backends before 0.5.7 have no such
 * route; the built-in defaults then apply. Only the newest read is kept, so a
 * slow answer from before a reconnect cannot replace the current backend's.
 */
export function primeGenerateBudget(): Promise<void> {
  const mine = ++generation;
  const read = (async () => {
    try {
      const body = await apiJson<ReportedGenerateBudget>('/generate/budget');
      if (mine === generation && body && typeof body === 'object') reported = body;
    } catch {
      // Keep the last known values.
    } finally {
      if (mine === generation) pending = null;
    }
  })();
  pending = read;
  return read;
}

export function resetGenerateBudgetForTests(): void {
  reported = {};
  pending = null;
  generation = 0;
}
