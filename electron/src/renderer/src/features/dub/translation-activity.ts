import { Store } from '@tanstack/store';

export interface TranslationRun {
  id: string;
  jobId: string;
  agent: string;
  target: string;
  purpose: 'translate' | 'fit';
  status: 'running' | 'complete' | 'failed' | 'cancelled';
  startedAt: number;
  endedAt?: number;
  logs: string;
  error?: string;
  rows: Array<{ id: string; source: string; text?: string; error?: string }>;
  retry?: () => Promise<unknown>;
}

export const translationActivity = new Store<{
  runs: TranslationRun[];
  expanded: boolean;
  tab: 'output' | 'logs';
}>({ runs: [], expanded: true, tab: 'output' });

export function startTranslationRun(run: Omit<TranslationRun, 'id' | 'status' | 'startedAt' | 'logs'>): string {
  const id = crypto.randomUUID();
  translationActivity.setState((state) => ({
    ...state,
    expanded: true,
    tab: state.runs.length ? state.tab : 'logs',
    runs: [...state.runs.filter((r) => r.jobId === run.jobId), { ...run, id, status: 'running', startedAt: Date.now(), logs: '' }],
  }));
  return id;
}

export function updateTranslationRun(id: string, update: Partial<TranslationRun>) {
  translationActivity.setState((state) => ({
    ...state,
    runs: state.runs.map((run) => run.id === id ? { ...run, ...update } : run),
  }));
}

export function appendTranslationLog(id: string, text: string) {
  translationActivity.setState((state) => ({
    ...state,
    runs: state.runs.map((run) => run.id === id && run.status === 'running'
      ? { ...run, logs: (run.logs + text).slice(-250_000) } : run),
  }));
}

export function finishTranslationRun(id: string, status: TranslationRun['status'], error?: string) {
  updateTranslationRun(id, { status, error, endedAt: Date.now() });
}
