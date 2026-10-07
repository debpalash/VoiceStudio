import { useEffect } from 'react';
import { isBackendReachable } from '@shared/utils/backendStage';
import { ENGINE_SELECTED_EVENT } from '@/lib/api/client';
import { primeGenerateBudget } from '@/lib/api/generate-budget';
import { useBackendStatus } from './use-backend-status';

/**
 * Re-reads the backend's generate budgets whenever the backend becomes
 * reachable or the active engine changes (the engine decides whether a take
 * computes on the CPU, which changes the budget the backstop must outlast).
 */
export function GenerateBudgetSync(): null {
  const reachable = isBackendReachable(useBackendStatus().stage);
  useEffect(() => {
    if (!reachable) return undefined;
    void primeGenerateBudget();
    const refresh = (): void => void primeGenerateBudget();
    window.addEventListener(ENGINE_SELECTED_EVENT, refresh);
    return () => window.removeEventListener(ENGINE_SELECTED_EVENT, refresh);
  }, [reachable]);
  return null;
}
