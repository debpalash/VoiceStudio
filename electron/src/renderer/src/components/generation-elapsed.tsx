import { useGenerationElapsed } from '@/hooks/use-generate';

/** Elapsed render time, subscribed on its own so ticks re-render only this label. */
export function GenerationElapsed({ prefix = '' }: { prefix?: string }) {
  const seconds = useGenerationElapsed();
  return <>{`${prefix}${seconds.toFixed(1)}s`}</>;
}
