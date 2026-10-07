/**
 * Client backstop for a single /generate request.
 *
 * The backend bounds every phase of a generate itself and answers with a
 * descriptive error when one runs out, so the client abort exists only for a
 * backend that has gone silent. It must therefore outlast the backend's
 * longest legitimate budget: aborting first reports a failure while the job is
 * still running (and later lands in history).
 *
 * The values mirror the backend defaults in backend/services/model_manager.py
 * and are held equal by tests/test_generate_abort_budget.py:
 *
 * - modelLoad: cold-load ceiling, OMNIVOICE_MODEL_LOAD_TIMEOUT
 * - queueWait: GPU queue wait, OMNIVOICE_GPU_QUEUE_TIMEOUT_S
 * - executionBase: the largest default execution base (sidecar receive
 *   timeouts reach 900 s; CPU hosts get 600 s, accelerated hosts 300 s)
 * - sidecarGrace: the grace generate_timeout_s adds for sidecar engines
 * - progressExtensionCap / progressExtensionBudgets: heartbeat extension,
 *   max(OMNIVOICE_PROGRESS_EXTENSION_CAP_S, budgets x execution budget)
 * - freeChars / charsPerSecond: the execution budget's length scaling
 * - cpuSecondsPerChar / cpuAutoCap: the default CPU budget's scaling and its
 *   ceiling (#2609), from backend/core/generate_budget.py
 * - textExpansionFactor: sizes the legacy length bonus for text the backend
 *   will lengthen (normalization, pronunciation rules)
 * - cpuAutoCeiling (reported only): the backend budgets the NORMALIZED text,
 *   whose expansion is unbounded (a six-digit number grows ~11x), so a CPU
 *   host running the default budget reports the automatic ceiling and the
 *   client waits for it instead of guessing from the typed length
 *
 * Operators can raise those budgets through the environment; the backend
 * reports its active values at GET /generate/budget, and the larger of each
 * reported value and its default is used. The margin covers scheduling jitter.
 */
export const BACKEND_GENERATE_BUDGET_S = {
  modelLoad: 1200,
  queueWait: 1800,
  executionBase: 900,
  sidecarGrace: 5,
  progressExtensionCap: 1800,
  progressExtensionBudgets: 3,
  freeChars: 1200,
  charsPerSecond: 40,
  cpuSecondsPerChar: 4,
  cpuAutoCap: 7200,
  textExpansionFactor: 16,
} as const;

const CLIENT_MARGIN_S = 60;

export type ReportedGenerateBudget = Partial<
  Record<
    'modelLoad' | 'queueWait' | 'executionBase' | 'progressExtensionCap' | 'cpuAutoCeiling',
    unknown
  >
>;

/** Milliseconds before the client gives up on a /generate for this text. */
export function generateAbortMs(textLength = 0, reported: ReportedGenerateBudget = {}): number {
  const raise = (
    key: 'modelLoad' | 'queueWait' | 'executionBase' | 'progressExtensionCap',
  ): number => {
    const value = reported[key];
    const base = BACKEND_GENERATE_BUDGET_S[key];
    return typeof value === 'number' && Number.isFinite(value) ? Math.max(base, value) : base;
  };
  const budget = {
    ...BACKEND_GENERATE_BUDGET_S,
    modelLoad: raise('modelLoad'),
    queueWait: raise('queueWait'),
    executionBase: raise('executionBase'),
    progressExtensionCap: raise('progressExtensionCap'),
  };
  // Mirrors client_execution_budget_s in backend/core/generate_budget.py: the
  // larger of the legacy length bonus and the default CPU budget's scaling.
  const chars = Math.max(0, textLength) * budget.textExpansionFactor;
  const legacy = budget.executionBase + Math.max(0, chars - budget.freeChars) / budget.charsPerSecond;
  const cpuAuto = Math.min(
    Math.max(legacy, budget.cpuSecondsPerChar * chars),
    Math.max(budget.cpuAutoCap, budget.executionBase),
  );
  const reportedCeiling = reported.cpuAutoCeiling;
  const ceiling =
    typeof reportedCeiling === 'number' && Number.isFinite(reportedCeiling) ? reportedCeiling : 0;
  const execution = Math.max(legacy, cpuAuto, ceiling) + budget.sidecarGrace;
  const extension = Math.max(
    budget.progressExtensionCap,
    budget.progressExtensionBudgets * execution,
  );
  return Math.ceil(
    (budget.modelLoad + budget.queueWait + execution + extension + CLIENT_MARGIN_S) * 1000,
  );
}
