import { useTranslation } from 'react-i18next';
import { useGenerationElapsed } from '@/hooks/use-generate';

/** Elapsed render time, subscribed on its own so ticks re-render only this label. */
export function GenerationElapsed({ prefix = '' }: { prefix?: string }) {
  const { t } = useTranslation();
  const seconds = useGenerationElapsed();
  return <>{`${prefix}${t('clone.duration_seconds', { seconds: seconds.toFixed(1) })}`}</>;
}
