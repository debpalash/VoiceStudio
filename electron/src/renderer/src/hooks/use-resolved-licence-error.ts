import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/lib/api/client';
import {
  modelLicenceRequirements,
  type ModelLicenceDetails,
} from '@/features/settings/model-license-contract';
import { modelLicenceDetailsKey } from '@/features/settings/model-licence-view';

/** Clear a blocked action's error once either dialog confirms all required terms. */
export function useResolvedLicenceError(error: unknown, onResolved: () => void) {
  const client = useQueryClient();
  useEffect(() => {
    const required = error instanceof ApiError ? modelLicenceRequirements(error.payload) : null;
    if (!required) return;
    const clearResolved = () => {
      if (required.every((model) => {
        const acceptance = client.getQueryData<ModelLicenceDetails>(
          modelLicenceDetailsKey(model.repo_id),
        )?.acceptance;
        return acceptance?.accepted && acceptance.fingerprint === model.fingerprint;
      })) onResolved();
    };
    const unsubscribe = client.getQueryCache().subscribe(clearResolved);
    clearResolved();
    return unsubscribe;
  }, [client, error, onResolved]);
}
