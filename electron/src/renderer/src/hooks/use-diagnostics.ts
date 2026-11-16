import { useEffect, useSyncExternalStore } from 'react';
import { getBridge } from '@/components/bridge';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import {
  getDiagnosticState,
  refreshDiagnosticState,
  subscribeDiagnostics,
} from '@/lib/diagnostic-reports';

/** Independent consent is readable through IPC before packaged Python exists. */
export function useDiagnostics() {
  const state = useSyncExternalStore(subscribeDiagnostics, getDiagnosticState, getDiagnosticState);
  const backend = useBackendStatus();
  useEffect(() => {
    if (getBridge()?.diagnostics || isBackendReachable(backend.stage))
      void refreshDiagnosticState();
  }, [backend.stage]);
  return state;
}
