import { AsyncLocalStorage, createHook } from 'node:async_hooks';
import type { DiagnosticReports } from './diagnostics';

type Reporter = Pick<DiagnosticReports, 'status'>;
interface Owner {
  reporter: Reporter;
  epoch: string | null;
}
const context = new AsyncLocalStorage<Owner>();
const owners = new WeakMap<object, Owner>();
const hook = createHook({
  init(_id, type, _trigger, resource) {
    const owner = context.getStore();
    if (type === 'PROMISE' && owner && resource) owners.set(resource, owner);
  },
});
let listeners = 0;

/** Track only promises created within an explicitly observed main operation. */
export function trackDiagnosticOperations(): () => void {
  if (listeners++ === 0) hook.enable();
  let closed = false;
  return () => {
    if (closed) return;
    closed = true;
    if (--listeners === 0) hook.disable();
  };
}

export function runDiagnosticOperation<T>(reports: Reporter, work: () => T): T {
  const inherited = context.getStore();
  // A continuation inside an earlier unit cannot borrow a later grant.
  const owner =
    inherited?.reporter === reports
      ? inherited
      : { reporter: reports, epoch: reports.status().grant_epoch ?? null };
  return context.run(owner, work);
}

export function diagnosticRejectionEpoch(promise: unknown, reports: Reporter): string | null {
  if (!promise || (typeof promise !== 'object' && typeof promise !== 'function')) return null;
  const owner = owners.get(promise);
  return owner?.reporter === reports ? owner.epoch : null;
}

export function diagnosticOperationOwner(reports: Reporter): { epoch: string | null } | null {
  const owner = context.getStore();
  if (!owner) return null;
  return { epoch: owner.reporter === reports ? owner.epoch : null };
}
