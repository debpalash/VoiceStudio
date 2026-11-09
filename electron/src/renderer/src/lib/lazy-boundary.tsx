import {
  Component,
  useEffect,
  useState,
  useSyncExternalStore,
  type ComponentProps,
  type ComponentType,
  type ErrorInfo,
  type ReactNode,
} from 'react';

/**
 * Renders `fallback` instead of unmounting the app when a lazily loaded child
 * fails (a chunk that cannot load). Remount it (change its key) to try again.
 */
export class LazyBoundary extends Component<
  { fallback: ReactNode; children: ReactNode; onError?: (error: unknown) => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown, _info: ErrorInfo) {
    console.warn('Lazy component failed to load', error);
    this.props.onError?.(error);
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/**
 * A code-split component that loads its module before it mounts, and whose
 * failed load can be retried by remounting (change the key of the enclosing
 * `LazyBoundary`, which renders its fallback on a failure).
 *
 * Deliberately not `React.lazy` + `<Suspense>`: React creates a suspended
 * tree's DOM when the module resolves but may commit it up to ~300 ms later
 * (Suspense reveal throttling). A media element in that DOM starts fetching its
 * source while still detached and fetches it again once mounted, so a preview
 * clip was requested two or three times. Here the module loads first and the
 * component mounts in one ordinary commit. `React.lazy` would also cache a
 * rejected import forever; this requests the chunk again on the next mount.
 */
export function retryableLazy<C extends ComponentType<any>>(
  load: () => Promise<{ default: C }>,
  placeholder: ReactNode = null,
): (props: ComponentProps<C>) => ReactNode {
  let loaded: C | null = null;
  let pending: Promise<C> | null = null;
  const listeners = new Set<() => void>();
  const subscribe = (listener: () => void) => {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  };
  const getSnapshot = () => loaded;
  const request = () =>
    (pending ??= load().then(
      (module) => {
        loaded = module.default;
        listeners.forEach((listener) => listener());
        return loaded;
      },
      (error: unknown) => {
        pending = null;
        throw error;
      },
    ));
  return function RetryableLazy(props: ComponentProps<C>) {
    // A shared store, so every mounted instance re-renders when the module
    // resolves, including one that resolved between this render and its effect.
    const Loaded = useSyncExternalStore(subscribe, getSnapshot) as ComponentType<any> | null;
    const [failure, setFailure] = useState<{ error: unknown } | null>(null);
    useEffect(() => {
      if (getSnapshot()) return;
      let live = true;
      request().catch((error: unknown) => live && setFailure({ error }));
      return () => {
        live = false;
      };
    }, []);
    if (failure) throw failure.error; // the enclosing LazyBoundary renders its fallback
    return Loaded ? <Loaded {...props} /> : placeholder;
  };
}
