import { TERMINAL_MODEL_INSTALL_STATES } from '@/hooks/use-model-install-sync';
import { GATED_TOPICS, describeInstall } from '@/lib/model-install-state';
import type { CatalogueModel } from '../model-catalogue-query';
import type { ModelOps } from './model-ops';

/** Everything a model row needs to know about its download job. */
export function describeJob(model: CatalogueModel, ops: ModelOps) {
  const job = ops.jobs.find((entry) => entry.repo_id === model.repo_id);
  const view = job ? describeInstall(job) : null;
  const failed = job?.state === 'failed' && !ops.isDismissed(model.repo_id);
  return {
    job,
    view,
    downloading: Boolean(job && !TERMINAL_MODEL_INSTALL_STATES.has(job.state)),
    cancelling: view?.stage === 'cancelling',
    failed,
    gatedFailure: failed && GATED_TOPICS.has(job?.docs_topic ?? ''),
    retryAfter: view?.retryAfter ?? 0,
    recovery: view?.recovery ?? null,
    pct: view?.pct ?? null,
  };
}

export type JobInfo = ReturnType<typeof describeJob>;
