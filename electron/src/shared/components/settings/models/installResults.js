/**
 * Bulk-install bookkeeping shared by every "install several models" button
 * (setup summary, recommendation card, model store). Pairs each
 * Promise.allSettled result with the model it was for BEFORE filtering, so a
 * failure is always reported against the right repository — filtering first
 * and then reading `missing[i]` would name the wrong one.
 */

/** Failed installs as `{ repo, error }`, in request order. */
export function failedInstalls(results, models) {
  return (results || [])
    .map((r, i) =>
      r?.status === 'rejected'
        ? { repo: models[i]?.repo_id ?? String(models[i]), error: r.reason }
        : null,
    )
    .filter(Boolean);
}

/** One line naming every failed repository and why. */
export function installFailureMessage(failed) {
  return failed.map((f) => `${f.repo}: ${f.error?.message || f.error}`).join(' · ');
}
