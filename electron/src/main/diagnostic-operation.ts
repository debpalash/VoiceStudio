import { AsyncLocalStorage, createHook } from 'node:async_hooks';
import { promiseHooks } from 'node:v8';
import { types } from 'node:util';
import type { DiagnosticReports } from './diagnostics';

type Reporter = Pick<DiagnosticReports, 'status'>;
interface Owner {
  reporter: Reporter;
  epoch: string | null;
  ambiguous?: boolean;
}
const context = new AsyncLocalStorage<Owner>();
const owners = new WeakMap<object, Owner>();
// Weak keys never keep an exception, its stack, or application objects alive.
// This is local authority metadata, never an event property.
const faultOwners = new WeakMap<Error, Owner>();
const nativePromise = Promise;
const nativePrototype = Promise.prototype;
const nativeThen = Promise.prototype.then;
const nativeSpecies = Object.getOwnPropertyDescriptor(Promise, Symbol.species)?.get;
const functionText = Function.prototype.toString;
const nativeFunction = (value: unknown): boolean =>
  typeof value === 'function' &&
  !types.isProxy(value) &&
  functionText.call(value).includes('[native code]');
/** Never invoke user constructors, species accessors or then getters to observe work. */
function plainNativePromise(value: unknown): value is Promise<unknown> {
  try {
    return (
      types.isPromise(value) &&
      Object.getPrototypeOf(value) === nativePrototype &&
      !Object.hasOwn(value, 'constructor') &&
      !Object.hasOwn(value, 'then') &&
      Object.getOwnPropertyDescriptor(nativePrototype, 'constructor')?.value === nativePromise &&
      nativeFunction(nativeThen) &&
      nativeFunction(nativeSpecies) &&
      Object.getOwnPropertyDescriptor(nativePromise, Symbol.species)?.get === nativeSpecies
    );
  } catch {
    return false;
  }
}
function isError(error: unknown): error is Error {
  try {
    return error instanceof Error;
  } catch {
    return false;
  }
}
function rememberFault(error: unknown, owner: Owner): void {
  try {
    if (isError(error) && !faultOwners.has(error))
      faultOwners.set(error, {
        reporter: owner.reporter,
        epoch: owner.ambiguous ? null : owner.epoch,
      });
  } catch {
    /* A hostile thrown object must preserve the original rejection. */
  }
}
const hook = createHook({
  init(_id, type, _trigger, resource) {
    const owner = context.getStore();
    if (type === 'PROMISE' && owner && resource) owners.set(resource, owner);
  },
});
let listeners = 0;
let stopParents: (() => void) | null = null;
let observingParent = false;
const observedParents = new WeakSet<Promise<unknown>>();
function observeConsumedParent(_promise: Promise<unknown>, parent?: Promise<unknown>): void {
  if (observingParent || !parent || observedParents.has(parent)) return;
  const current = context.getStore();
  const inherited = owners.get(parent);
  // V8 also supplies the outer async Promise as a non-Promise await wrapper's
  // parent. That ancestry is not proof of a consumer: observing it would handle
  // an otherwise unhandled rejection. Same-operation work already has this
  // exact authority; only crossing ownership needs an extra fault stamp.
  if (current && inherited === current) return;
  const owner = inherited ?? (current ? { reporter: current.reporter, epoch: null } : null);
  if (!owner) return;
  if (!plainNativePromise(parent)) {
    (current ?? owner).ambiguous = true;
    return;
  }
  observingParent = true;
  try {
    // V8 calls init before registering the actual consumer's reaction. This
    // passive rejection observer stamps an old/unknown fault before forwarding;
    // successful old data stamps nothing. Its child always fulfills, and the
    // parent was already being consumed, so fatal/unhandled policy is unchanged.
    nativeThen.call(
      parent,
      () => undefined,
      (error: unknown) => {
        rememberFault(error, owner);
      },
    );
    observedParents.add(parent);
  } catch {
    (current ?? owner).ambiguous = true;
  } finally {
    observingParent = false;
  }
}

/** Track only promises created within an explicitly observed main operation. */
export function trackDiagnosticOperations(): () => void {
  if (listeners++ === 0) {
    hook.enable();
    stopParents = promiseHooks.createHook({ init: observeConsumedParent }) as () => void;
  }
  let closed = false;
  return () => {
    if (closed) return;
    closed = true;
    if (--listeners === 0) {
      stopParents?.();
      stopParents = null;
      hook.disable();
    }
  };
}

export function runDiagnosticOperation<T>(reports: Reporter, work: () => T): T {
  const inherited = context.getStore();
  // A continuation inside an earlier unit cannot borrow a later grant.
  const owner =
    inherited?.reporter === reports
      ? inherited
      : { reporter: reports, epoch: reports.status().grant_epoch ?? null };
  return context.run(owner, () => {
    let result: T;
    try {
      result = work();
    } catch (error) {
      rememberFault(error, owner);
      throw error;
    }
    // Preserve the actual result/Promise. A forwarding Promise would assimilate
    // fulfilled objects again and can invoke a newly installed then getter.
    // Actual consumers stamp rejected faults via observeConsumedParent; a direct
    // unhandled rejection is stamped when its existing owner is looked up.
    if (types.isPromise(result)) {
      const original = owners.get(result);
      if (original && !plainNativePromise(result)) original.ambiguous = true;
    }
    return result;
  });
}

export function diagnosticRejectionEpoch(
  promise: unknown,
  reports: Reporter,
  error?: unknown,
): string | null {
  if (arguments.length >= 3) {
    if (!isError(error)) return null;
    const first = faultOwners.get(error);
    if (first) return first.reporter === reports ? first.epoch : null;
  }
  if (!promise || (typeof promise !== 'object' && typeof promise !== 'function')) return null;
  const owner = owners.get(promise);
  if (owner && arguments.length >= 3) rememberFault(error, owner);
  return owner?.reporter === reports && !owner.ambiguous ? owner.epoch : null;
}

export function diagnosticOperationOwner(
  reports: Reporter,
  error?: unknown,
): { epoch: string | null } | null {
  if (arguments.length >= 2) {
    if (!isError(error)) return null;
    const first = faultOwners.get(error);
    if (first) return { epoch: first.reporter === reports ? first.epoch : null };
  }
  const owner = context.getStore();
  if (!owner) return null;
  return { epoch: owner.reporter === reports && !owner.ambiguous ? owner.epoch : null };
}
