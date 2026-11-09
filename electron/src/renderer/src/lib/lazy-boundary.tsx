import {
  Component,
  lazy,
  type ComponentProps,
  type ComponentType,
  type ErrorInfo,
  type JSX,
  type ReactNode,
} from 'react';

/**
 * Renders `fallback` instead of unmounting the app when a lazily loaded child
 * fails (a chunk that cannot load). Remount it (change its key) to try again.
 */
export class LazyBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown, _info: ErrorInfo) {
    console.warn('Lazy component failed to load', error);
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/**
 * `React.lazy` caches a rejected import forever; this drops the cache on
 * failure so the next mount requests the chunk again.
 */
export function retryableLazy<C extends ComponentType<any>>(
  load: () => Promise<{ default: C }>,
): (props: ComponentProps<C>) => JSX.Element {
  const make = (): ComponentType<any> =>
    lazy(() =>
      load().catch((error: unknown) => {
        current = make();
        throw error;
      }),
    );
  let current = make();
  return (props) => {
    const Current = current;
    return <Current {...props} />;
  };
}
