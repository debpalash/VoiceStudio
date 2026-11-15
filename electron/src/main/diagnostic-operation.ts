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
  const current = context.getStore();
  if (observingParent || !parent || !current || observedParents.has(parent)) return;
  if (!plainNativePromise(parent)) {
    current.ambiguous = true;
    return;
  }
  const owner = owners.get(parent) ?? { reporter: current.reporter, epoch: null };
  observingParent = true;
  try {
    // V8 calls init before registering the actual consumer's reaction. This
    // passive rejection observer stamps an old/unknown fault before forwarding;
    // successful old data stamps nothing. Its child always fulfills, and the
    // parent was already being consumed, so fatal/unhandled policy is unchanged.
    nativeThen.call(parent, undefined, (error: unknown) => {
      rememberFault(error, owner);
    });
    observedParents.add(parent);
  } catch {
    current.ambiguous = true;
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
    // Unknown work already in flight cannot be adopted by a new action. Only
    // promises observed at their actual producer boundary receive a wrapper.
    if (!types.isPromise(result)) return result;
    const original = owners.get(result);
    if (!original) return result;
    if (!plainNativePromise(result)) {
      original.ambiguous = true;
      return result;
    }
    const observed = nativeThen.call(result, undefined, (error: unknown) => {
      // A propagated/rethrown old fault keeps its first owner, including OFF.
      // Successful old data carries no fault authority into a new operation.
      rememberFault(error, original);
      throw error;
    });
    owners.set(observed, original);
    return observed as T;
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
