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
interface ParentObservation {
  owner: Owner;
  started: boolean;
  done: boolean;
}
interface FaultOwner extends Owner {
  pending?: ParentObservation;
}
const faultOwners = new WeakMap<Error, FaultOwner>();
// Entries exist only until the child settles or begins its actual reaction.
let pendingConsumers = new WeakMap<Promise<unknown>, {
  parent: Promise<unknown>;
  observation: ParentObservation;
}>();
const reactionStack: (ParentObservation | undefined)[] = [];
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
function rememberFault(error: unknown, owner: Owner, observed?: ParentObservation): void {
  try {
    if (!isError(error)) return;
    const first = faultOwners.get(error);
    if (!first || (observed && first.pending === observed)) {
      // A synchronous AsyncResource escape can throw before the passive
      // observer microtask runs. Keep that provisional attribution local until
      // this exact parent confirms whether it supplied the same Error.
      const pending = observed ? undefined : reactionStack.at(-1);
      faultOwners.set(error, {
        reporter: owner.reporter,
        epoch: owner.ambiguous ? null : owner.epoch,
        ...(pending && !pending.done ? { pending } : {}),
      });
    }
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
let observedParents = new WeakMap<Promise<unknown>, ParentObservation>();
function observeParent(parent: Promise<unknown>, observation: ParentObservation): void {
  if (observation.started) return;
  observation.started = true;
  const current = context.getStore();
  if (!plainNativePromise(parent)) {
    (current ?? observation.owner).ambiguous = true;
    return;
  }
  observingParent = true;
  try {
    // An actual consumer is now registered. Ignore fulfillment data entirely;
    // returning it would assimilate a user object's then getter a second time.
    nativeThen.call(
      parent,
      () => { observation.done = true; },
      (error: unknown) => {
        rememberFault(error, observation.owner, observation);
        observation.done = true;
      },
    );
  } catch {
    (current ?? observation.owner).ambiguous = true;
  } finally {
    observingParent = false;
  }
}
function observeConsumedParent(promise: Promise<unknown>, parent?: Promise<unknown>): void {
  if (observingParent || !parent) return;
  const current = context.getStore();
  const inherited = owners.get(parent);
  const owner = inherited ?? (current ? { reporter: current.reporter, epoch: null } : null);
  if (!owner) return;
  let observation = observedParents.get(parent);
  if (!observation) {
    observation = { owner, started: false, done: false };
    observedParents.set(parent, observation);
  }
  if (current && inherited === current) {
    // V8 also calls init for await wrappers parented by the outer async
    // Promise. Only before(child) confirms a real reaction; wrappers settle
    // without running one and must never handle the unconsumed outer Promise.
    pendingConsumers.set(promise, { parent, observation });
    return;
  }
  // Cross-owner/outside consumers retain their observer before the consumer's
  // own reaction is registered, preserving already-proven fault ownership.
  observeParent(parent, observation);
}

/** Track only promises created within an explicitly observed main operation. */
export function trackDiagnosticOperations(): () => void {
  if (listeners++ === 0) {
    hook.enable();
    stopParents = promiseHooks.createHook({
      init: observeConsumedParent,
      before(promise) {
        const consumer = pendingConsumers.get(promise);
        pendingConsumers.delete(promise);
        reactionStack.push(consumer?.observation);
        if (consumer) observeParent(consumer.parent, consumer.observation);
      },
      after() { reactionStack.pop(); },
      settled(promise) {
        // Drop non-consumer await wrappers and their temporary parent reference.
        pendingConsumers.delete(promise);
      },
    }) as () => void;
  }
  let closed = false;
  return () => {
    if (closed) return;
    closed = true;
    if (--listeners === 0) {
      stopParents?.();
      stopParents = null;
      hook.disable();
      reactionStack.length = 0;
      // Disabling hooks also releases temporary parent links whose child never
      // got a reaction/settlement callback. Fault metadata remains weak/local.
      pendingConsumers = new WeakMap();
      observedParents = new WeakMap();
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
    if (first) return first.pending && !first.pending.done ? null : first.reporter === reports ? first.epoch : null;
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
    if (first) return { epoch: first.pending && !first.pending.done ? null : first.reporter === reports ? first.epoch : null };
  }
  const owner = context.getStore();
  if (!owner) return null;
  return { epoch: owner.reporter === reports && !owner.ambiguous ? owner.epoch : null };
}
